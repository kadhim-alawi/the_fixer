"""Running a mission through ADK.

Day 3 replaces the single agent here with the orchestrator/investigator/
executor/verifier arrangement. The event plumbing, tool binding and streaming
interface stay the same, so this is the seam the rest of the system builds on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from .. import tools as T
from ..sim.world import World
from .model import require
from .prompts import MISSION_INSTRUCTION, objective_prompt

APP_NAME = "the-fixer"


@dataclass
class MissionEvent:
    """One thing that happened, in a form Mission Control can render."""

    seq: int
    kind: str  # thought | tool_call | tool_result | action | message | error
    text: str
    tool: str | None = None
    args: dict | None = None
    result: object | None = None
    risk: str | None = None
    sim_time: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


@dataclass
class MissionResult:
    mission_id: str
    objective: str
    events: list[MissionEvent] = field(default_factory=list)
    final_text: str = ""
    tool_calls: list[T.ToolCall] = field(default_factory=list)
    error: str | None = None

    @property
    def actions_taken(self) -> list[T.ToolCall]:
        return [c for c in self.tool_calls if c.meta.kind == "act" and not c.denied]


def build_agent(name: str = "the_fixer", tools=None) -> LlmAgent:
    backend = require()
    return LlmAgent(
        model=backend.model,
        name=name,
        description="Autonomous operations agent that investigates, fixes and verifies.",
        instruction=MISSION_INSTRUCTION,
        tools=list(tools if tools is not None else T.ALL_TOOLS),
    )


async def run_mission(
    world: World,
    objective: str,
    *,
    guard: Callable[[T.ToolMeta, dict], str | None] | None = None,
    on_event: Callable[[MissionEvent], None] | None = None,
) -> MissionResult:
    """Run one mission to completion and return everything it did."""
    result = MissionResult(mission_id=f"m-{uuid.uuid4().hex[:8]}", objective=objective)
    async for ev in stream_mission(world, objective, guard=guard, result=result):
        if on_event:
            on_event(ev)
    return result


async def stream_mission(
    world: World,
    objective: str,
    *,
    guard: Callable[[T.ToolMeta, dict], str | None] | None = None,
    result: MissionResult | None = None,
) -> AsyncIterator[MissionEvent]:
    """Run a mission, yielding events as they happen.

    The tool environment is bound to this coroutine's context, so tools reach
    the right world without any of it passing through the model.
    """
    result = result or MissionResult(mission_id=f"m-{uuid.uuid4().hex[:8]}", objective=objective)
    env = T.ToolEnv(world=world, guard=guard)
    T.set_env(env)
    result.tool_calls = env.calls

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
        ev = MissionEvent(
            seq=seq,
            kind=kind,
            text=text,
            sim_time=world.now().isoformat(timespec="seconds"),
            **kw,
        )
        result.events.append(ev)
        return ev

    yield emit("message", f"Mission accepted: {objective}")

    try:
        async for event in runner.run_async(
            user_id="operator",
            session_id=result.mission_id,
            new_message=types.Content(
                role="user", parts=[types.Part(text=objective_prompt(objective))]
            ),
        ):
            for part in (event.content.parts if event.content and event.content.parts else []):
                if getattr(part, "function_call", None):
                    fc = part.function_call
                    meta = T.REGISTRY.get(fc.name)
                    yield emit(
                        "action" if meta and meta.kind == "act" else "tool_call",
                        f"{fc.name}({_brief_args(dict(fc.args or {}))})",
                        tool=fc.name,
                        args=dict(fc.args or {}),
                        risk=meta.risk if meta else None,
                    )
                elif getattr(part, "function_response", None):
                    fr = part.function_response
                    yield emit(
                        "tool_result",
                        _brief_result(fr.response),
                        tool=fr.name,
                        result=fr.response,
                    )
                elif getattr(part, "text", None):
                    text = part.text.strip()
                    if text:
                        result.final_text = text
                        yield emit("thought", text)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        yield emit("error", result.error)


def _brief_args(args: dict, width: int = 90) -> str:
    s = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return s if len(s) <= width else s[: width - 3] + "..."


def _brief_result(resp, width: int = 160) -> str:
    s = str(resp)
    return s if len(s) <= width else s[: width - 3] + "..."
