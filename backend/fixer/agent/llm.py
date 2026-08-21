"""Running a mission with the model in the driving seat.

Same world, same tools, same Mission object as the heuristic agent in
``oracle.py``. The only difference is who decides what to do next -- which is
exactly what the evaluation is trying to measure, so everything else must be
held constant between them.
"""

from __future__ import annotations

from typing import AsyncIterator, Callable

from google.adk.agents import LlmAgent, RunConfig
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .. import tools as T
from ..mission import Mission, MissionStatus
from ..sim.world import World
from .model import require
from .prompts import MISSION_INSTRUCTION, objective_prompt
from .runner import MissionEvent

APP_NAME = "the-fixer"

# A mission that has not concluded after this many model turns is stopped. An
# agent looping without converging is a failure mode we want recorded as
# SAFETY_LIMIT rather than left running.
MAX_LLM_CALLS = 60


def build_agent(name: str = "the_fixer") -> LlmAgent:
    backend = require()
    return LlmAgent(
        model=backend.model,
        name=name,
        description="Autonomous operations agent that investigates, fixes and verifies.",
        instruction=MISSION_INSTRUCTION,
        tools=list(T.ALL_TOOLS),
    )


async def stream_llm_mission(
    world: World,
    objective: str,
    *,
    mission: Mission | None = None,
    fast_forward: bool = False,
    guard: Callable[[T.ToolMeta, dict], str | None] | None = None,
) -> AsyncIterator[MissionEvent]:
    mission = mission or Mission(
        objective=objective,
        success_criteria=[
            "root cause identified",
            "remediation applied",
            "objective metric recovered and verified",
        ],
    )
    env = T.ToolEnv(
        world=world, mission=mission, guard=guard, fast_forward=fast_forward
    )
    T.set_env(env)

    agent = build_agent()
    runner = Runner(
        app_name=APP_NAME,
        agent=agent,
        session_service=InMemorySessionService(),
        auto_create_session=True,
    )

    seq = 0

    def emit(kind: str, text: str, **kw) -> MissionEvent:
        nonlocal seq
        seq += 1
        return MissionEvent(
            seq=seq,
            kind=kind,
            text=text,
            sim_time=world.now().isoformat(timespec="seconds"),
            **kw,
        )

    yield emit("message", f"Mission accepted: {objective}")

    try:
        async for event in runner.run_async(
            user_id="operator",
            session_id=mission.id,
            new_message=types.Content(
                role="user", parts=[types.Part(text=objective_prompt(objective))]
            ),
            run_config=RunConfig(max_llm_calls=MAX_LLM_CALLS),
        ):
            parts = event.content.parts if event.content and event.content.parts else []
            for part in parts:
                fc = getattr(part, "function_call", None)
                fr = getattr(part, "function_response", None)
                text = getattr(part, "text", None)

                if fc:
                    meta = T.REGISTRY.get(fc.name)
                    kind = "action" if meta and meta.kind == "act" else "tool_call"
                    yield emit(
                        kind,
                        f"{fc.name}({_args(dict(fc.args or {}))})",
                        tool=fc.name,
                        args=dict(fc.args or {}),
                        risk=meta.risk if meta else None,
                    )
                elif fr:
                    yield emit(
                        "tool_result",
                        _summarise(fr.name, fr.response),
                        tool=fr.name,
                        result=fr.response,
                    )
                elif text and text.strip():
                    yield emit("thought", text.strip())
    except Exception as exc:
        yield emit("error", f"{type(exc).__name__}: {exc}")

    # The agent is expected to call conclude_mission. If it stopped without
    # doing so, that is not a success -- record what actually happened.
    if not mission.status.terminal:
        mission.conclude(
            MissionStatus.SAFETY_LIMIT if seq >= MAX_LLM_CALLS else MissionStatus.FAILED,
            root_cause="",
            evidence_summary="Agent stopped without concluding the mission.",
            before_after="",
            sim_time=world.now().isoformat(timespec="seconds"),
        )
        yield emit("message", f"Mission ended without a conclusion: {mission.status.value}")
    else:
        yield emit("message", f"Mission {mission.status.value}")


async def run_llm_mission(
    world: World,
    objective: str,
    *,
    mission: Mission | None = None,
    fast_forward: bool = False,
    guard: Callable[[T.ToolMeta, dict], str | None] | None = None,
) -> Mission:
    mission = mission or Mission(objective=objective)
    async for _ in stream_llm_mission(
        world, objective, mission=mission, fast_forward=fast_forward, guard=guard
    ):
        pass
    return mission


def _args(args: dict, width: int = 90) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= width else s[: width - 3] + "..."


def _summarise(name: str, resp) -> str:
    """One legible line per tool result, for the timeline.

    The raw payloads are large; a judge watching should see the number that
    mattered, not a wall of JSON. The full result stays on the ledger.
    """
    if not isinstance(resp, dict):
        return str(resp)[:160]
    if "error" in resp:
        return f"refused: {resp.get('reason') or resp['error']}"
    if name == "check_conversion":
        cur, ref = resp.get("current", {}), resp.get("reference_24h_ago", {})
        return (
            f"{resp.get('platform', 'all')}: {cur.get('rate_pct')}% now vs "
            f"{ref.get('rate_pct')}% baseline "
            f"({resp.get('change', {}).get('relative_pct')}%), "
            f"n={cur.get('sessions')}"
        )
    if name == "query_conversion_funnel" and resp.get("segments"):
        return "  ".join(
            f"{s.get(resp.get('split_by', 'segment'))}={s['conversion_rate_pct']}%"
            for s in resp["segments"][:4]
        )
    if name == "query_payments":
        top = resp.get("failures_by_error_code", [])[:3]
        return f"{resp.get('failure_rate_pct')}% failures: " + ", ".join(
            f"{c['error_code']}x{c['count']}" for c in top
        )
    if name == "query_configuration":
        ch = [e for e in resp.get("entries", []) if e.get("changed")]
        return f"{len(ch)} keys changed recently"
    if name == "query_deployments":
        d = resp.get("deployments", [])
        return f"{len(d)} deployments" + (
            f", most recent #{d[0]['ref']} {d[0]['minutes_ago']}m ago" if d else ""
        )
    if name == "wait_for_traffic":
        return f"waited {resp.get('waited_minutes')}m, {resp.get('sessions_since')} new sessions"
    if name in ("record_hypothesis", "revise_hypothesis"):
        cur = resp.get("current", [])
        return "  ".join(f"{h['id']}={h['confidence']} {h['state']}" for h in cur)
    if name == "assess_remediation":
        return (
            f"assessed {resp.get('assessed_action')}, "
            f"failed_remediations={resp.get('failed_remediations')}"
        )
    if name == "conclude_mission":
        c = resp.get("concluded", {})
        return f"{c.get('status')}: {c.get('root_cause', '')[:100]}"
    if "applied" in resp:
        return f"{'applied' if resp['applied'] else 'not applied'}: {resp.get('detail', '')}"
    s = str(resp)
    return s if len(s) <= 160 else s[:157] + "..."
