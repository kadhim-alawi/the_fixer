"""Tool plumbing: registry, safety metadata, execution context, time windows.

Design rules for every tool in this package:

* **Structured in, structured out.** The agent never writes SQL and never sees a
  raw row. It picks a tool and passes typed arguments. This is what makes the
  agent's behaviour reproducible and its permissions enforceable.
* **Counts travel with rates.** Every metric result carries the sample size it
  was computed from, so the agent can tell a real movement from thin data.
* **Nothing leaks the answer.** No tool knows what the incident is, when it
  started, or whether the mission is solved. Comparisons are always to a normal
  analytics reference (the same window yesterday), never to "before the
  incident".
"""

from __future__ import annotations

import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Literal

from ..mission import Mission
from ..sim.world import World

Permission = Literal["READ", "WRITE", "EXECUTE"]
Risk = Literal["LOW", "MEDIUM", "HIGH"]
Reversibility = Literal["REVERSIBLE", "PARTIAL", "IRREVERSIBLE"]


@dataclass(frozen=True)
class ToolMeta:
    name: str
    kind: Literal["read", "act", "verify", "reason"]
    permission: Permission
    risk: Risk
    reversibility: Reversibility

    @property
    def requires_approval(self) -> bool:
        """High risk, or anything that cannot be undone, needs a human."""
        return self.risk == "HIGH" or self.reversibility == "IRREVERSIBLE"


REGISTRY: dict[str, ToolMeta] = {}
FUNCTIONS: dict[str, Callable] = {}


def tool(
    *,
    kind: Literal["read", "act", "verify", "reason"],
    permission: Permission,
    risk: Risk = "LOW",
    reversibility: Reversibility = "REVERSIBLE",
) -> Callable[[Callable], Callable]:
    """Register a tool and attach its safety metadata.

    The function is returned unchanged: ADK builds its schema from the real
    signature and docstring, so nothing may wrap or obscure them.
    """

    def deco(fn: Callable) -> Callable:
        REGISTRY[fn.__name__] = ToolMeta(
            name=fn.__name__,
            kind=kind,
            permission=permission,
            risk=risk,
            reversibility=reversibility,
        )
        FUNCTIONS[fn.__name__] = fn
        return fn

    return deco


# ---------------------------------------------------------------------------
# Execution context
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    seq: int
    name: str
    args: dict[str, Any]
    result: Any
    meta: ToolMeta
    sim_time: datetime
    duration_ms: int
    denied: str | None = None


@dataclass
class ToolEnv:
    """Per-mission tool environment.

    Holds the world the tools act on and the ledger of everything they did.
    The ledger is the evidence trail: Mission Control renders it, and the
    evaluation harness scores against it.
    """

    world: World
    mission: Mission | None = None
    calls: list[ToolCall] = field(default_factory=list)
    # Set by the safety gate (Day 7) to block a call before it executes.
    guard: Callable[[ToolMeta, dict], str | None] | None = None
    # Evaluation runs hundreds of missions and cannot spend real time
    # waiting; when true, waits jump the sim clock instead of sleeping.
    fast_forward: bool = False

    def record(
        self,
        name: str,
        args: dict,
        result: Any,
        duration_ms: int,
        denied: str | None = None,
    ) -> ToolCall:
        call = ToolCall(
            seq=len(self.calls) + 1,
            name=name,
            args=args,
            result=result,
            meta=REGISTRY[name],
            sim_time=self.world.now(),
            duration_ms=duration_ms,
            denied=denied,
        )
        self.calls.append(call)
        return call


_env: ContextVar[ToolEnv | None] = ContextVar("fixer_tool_env", default=None)


def set_env(env: ToolEnv) -> None:
    _env.set(env)


def get_env() -> ToolEnv:
    env = _env.get()
    if env is None:
        raise RuntimeError("No ToolEnv bound; a mission must be running.")
    return env


async def invoke(name: str, args: dict, body: Callable) -> dict:
    """Run a tool body with recording, timing and the safety gate applied.

    Every tool routes through here so that the ledger cannot be bypassed --
    including by a tool that fails.
    """
    env = get_env()
    meta = REGISTRY[name]

    if env.guard is not None:
        denial = env.guard(meta, args)
        if denial:
            result = {"error": "not_permitted", "reason": denial}
            env.record(name, args, result, 0, denied=denial)
            return result

    await env.world.tick()
    t0 = time.perf_counter()
    try:
        result = await body()
    except Exception as exc:  # surfaced to the agent, not raised at it
        result = {"error": type(exc).__name__, "detail": str(exc)}
    dt = int((time.perf_counter() - t0) * 1000)
    env.record(name, args, result, dt)

    # Remediations land on the mission automatically. Relying on the agent to
    # also report its own actions would make the record of what happened
    # dependent on the agent choosing to be honest about it.
    if meta.kind == "act" and env.mission is not None and isinstance(result, dict):
        env.mission.record_action(
            tool=name,
            args=args,
            risk=meta.risk,
            reversibility=meta.reversibility,
            applied=bool(result.get("applied")),
            sim_time=env.world.now().isoformat(timespec="seconds"),
        )
    return result


# ---------------------------------------------------------------------------
# Time windows
# ---------------------------------------------------------------------------


def window(
    world: World, window_minutes: int, ending_minutes_ago: int = 0
) -> tuple[datetime, datetime]:
    """Resolve a sim-time window.

    Windows are expressed the way an operator would say them out loud -- "the
    last 30 minutes", "the hour that ended 24 hours ago" -- rather than as
    absolute timestamps the agent would have to compute.
    """
    end = world.now() - timedelta(minutes=max(0, ending_minutes_ago))
    start = end - timedelta(minutes=max(1, window_minutes))
    return start, end


def pct(numerator: int | None, denominator: int | None) -> float | None:
    if not denominator:
        return None
    return round(100.0 * (numerator or 0) / denominator, 3)
