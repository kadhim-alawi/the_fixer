"""Mission sessions: running missions in the background and fanning out events.

A mission runs as a task. Everything it does is appended to a transcript and
pushed to any connected viewer, so a judge watching Mission Control sees the
same stream the agent is producing, as it happens -- not a replay assembled
afterwards.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import Integer, and_, func, select

from .. import tools as T
from ..agent import model as model_cfg
from ..mission import Mission, MissionStatus
from ..sim.schema import Session
from ..sim.world import World, build_world

OBJECTIVE = (
    "Our conversion rate has dropped significantly today. "
    "Find out why and fix the problem."
)


# How long the agent waits for an operator before giving up on a high-risk
# action. It must be finite: an agent blocked forever on a dialog nobody is
# watching is a hang, not a safety feature.
APPROVAL_TIMEOUT_SECONDS = 180


@dataclass
class Approval:
    """A high-risk action, paused, waiting on a human."""

    id: str
    tool: str
    args: dict
    risk: str
    reversibility: str
    reason: str
    decided: bool = False
    approved: bool = False
    timed_out: bool = False
    # Not serialisable, and not anyone else's business.
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def payload(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "event"}


@dataclass
class MissionSession:
    id: str
    objective: str
    incident_key: str
    agent: str
    world: World
    engine: Any
    mission: Mission
    events: list[dict] = field(default_factory=list)
    subscribers: list[asyncio.Queue] = field(default_factory=list)
    state: str = "starting"  # starting | running | done | error
    error: str | None = None
    pending_approval: Approval | None = None
    approvals: list[Approval] = field(default_factory=list)
    task: asyncio.Task | None = None

    # -- fan-out ------------------------------------------------------------

    def publish(self, payload: dict) -> None:
        self.events.append(payload)
        for q in list(self.subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        # A viewer joining late still sees the whole mission.
        for e in self.events:
            q.put_nowait(e)
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.subscribers:
            self.subscribers.remove(q)

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "objective": self.objective,
            "agent": self.agent,
            "state": self.state,
            "error": self.error,
            "mission": self.mission.as_dict(),
            "pending_approval": (
                self.pending_approval.payload() if self.pending_approval else None
            ),
            "approvals": [a.payload() for a in self.approvals],
            "sim_time": self.world.now().isoformat(timespec="seconds")
            if self.world.scenario
            else None,
        }


class MissionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, MissionSession] = {}

    async def start(
        self,
        objective: str = OBJECTIVE,
        incident_key: str = "payment_config_regression",
        seed: int = 4242,
        speed: float = 180.0,
        agent: str | None = None,
    ) -> MissionSession:
        mid = f"m-{uuid.uuid4().hex[:8]}"
        engine, world = await build_world(
            f"./missions/{mid}.db",
            incident_key=incident_key,
            seed=seed,
            speed=speed,
        )
        backend = model_cfg.resolve()
        chosen = agent or ("llm" if backend.ready else "oracle")
        mission = Mission(
            objective=objective,
            success_criteria=[
                "root cause identified",
                "remediation applied",
                "objective metric recovered and verified",
            ],
        )
        s = MissionSession(
            id=mid,
            objective=objective,
            incident_key=incident_key,
            agent=chosen,
            world=world,
            engine=engine,
            mission=mission,
        )
        self.sessions[mid] = s
        s.task = asyncio.create_task(self._run(s))
        return s

    def get(self, mid: str) -> MissionSession | None:
        return self.sessions.get(mid)

    # -- the approval gate --------------------------------------------------

    def _guard(self, s: MissionSession):
        """Pause a high-risk action and put it in front of a human.

        The agent does not take the action and report it afterwards -- it stops,
        mid-action, and waits. That is the difference between an audit log and a
        control.

        The wait is bounded. If nobody answers, the action is refused and the
        agent is told why, so an unattended mission degrades into "carried on
        without the dangerous step" rather than hanging.
        """

        async def guard(meta: T.ToolMeta, args: dict) -> str | None:
            if not meta.requires_approval:
                return None

            # An operator who already approved this tool does not get asked
            # again for the same mission.
            if any(a.tool == meta.name and a.decided and a.approved for a in s.approvals):
                return None

            ap = Approval(
                id=f"ap-{uuid.uuid4().hex[:6]}",
                tool=meta.name,
                args={k: v for k, v in args.items() if k != "reason"},
                risk=meta.risk,
                reversibility=meta.reversibility,
                reason=str(args.get("reason", "")),
            )
            s.approvals.append(ap)
            s.pending_approval = ap
            s.publish(
                {"type": "approval_required", "approval": ap.payload(), "snapshot": s.snapshot()}
            )

            try:
                await asyncio.wait_for(ap.event.wait(), timeout=APPROVAL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                ap.decided, ap.approved, ap.timed_out = True, False, True
                s.pending_approval = None
                s.publish(
                    {"type": "approval_timeout", "approval": ap.payload(), "snapshot": s.snapshot()}
                )
                return (
                    f"{meta.name} is {meta.risk} risk and {meta.reversibility}, so it "
                    f"needs an operator's approval. None was given within "
                    f"{APPROVAL_TIMEOUT_SECONDS} seconds, so it was not carried out. "
                    "Continue without it, or end the mission as REQUIRES_HUMAN if you "
                    "cannot achieve the objective without it."
                )

            if ap.approved:
                return None
            return (
                f"An operator reviewed {meta.name} and rejected it. Do not attempt it "
                "again. Continue without it, or end the mission as REQUIRES_HUMAN if "
                "you cannot achieve the objective without it."
            )

        return guard

    def decide(self, mid: str, approval_id: str, approved: bool) -> bool:
        s = self.sessions.get(mid)
        if not s:
            return False
        ap = next((a for a in s.approvals if a.id == approval_id), None)
        if not ap:
            return False
        ap.decided, ap.approved = True, approved
        if s.pending_approval and s.pending_approval.id == approval_id:
            s.pending_approval = None
        ap.event.set()  # releases the paused agent
        s.publish(
            {
                "type": "approval_decided",
                "approval": ap.payload(),
                "snapshot": s.snapshot(),
            }
        )
        return True

    # -- running ------------------------------------------------------------

    async def _run(self, s: MissionSession) -> None:
        s.state = "running"
        s.publish({"type": "started", "snapshot": s.snapshot()})
        try:
            if s.agent == "oracle":
                from ..agent.oracle import run_oracle

                gen = run_oracle(
                    s.world,
                    s.objective,
                    mission=s.mission,
                    fast_forward=False,
                    guard=self._guard(s),
                )
                async for ev, _m in gen:
                    s.publish(
                        {"type": "event", "event": ev.as_dict(), "snapshot": s.snapshot()}
                    )
            else:
                from ..agent.llm import stream_llm_mission

                async for ev in stream_llm_mission(
                    s.world,
                    s.objective,
                    mission=s.mission,
                    fast_forward=False,
                    guard=self._guard(s),
                ):
                    s.publish(
                        {"type": "event", "event": ev.as_dict(), "snapshot": s.snapshot()}
                    )
            s.state = "done"
        except Exception as exc:  # a crashed mission must still be visible
            s.state = "error"
            s.error = f"{type(exc).__name__}: {exc}"
            s.publish({"type": "error", "error": s.error, "snapshot": s.snapshot()})
        finally:
            for a in s.approvals:
                a.event.set()
            s.pending_approval = None
            if s.state != "error":
                s.state = "done"
            s.publish({"type": "finished", "snapshot": s.snapshot()})

    # -- the chart ----------------------------------------------------------

    async def metrics(self, s: MissionSession, hours: int = 6, buckets: int = 48) -> dict:
        """Conversion over time, overall and per platform.

        This is the before-and-after picture. It reads the same rows the agent's
        tools read -- there is no separate presentation copy of the numbers.
        """
        w = s.world
        if w.scenario is None:
            return {"series": []}
        await w.tick()
        end = w.now()
        start = end - timedelta(hours=hours)
        step = (end - start) / buckets

        # Each point is a trailing 30-minute rate, not the rate inside one
        # 7-minute bucket. A bucket that narrow holds only a few hundred
        # sessions per platform, so the line jitters by 20% on sampling noise
        # alone and the actual drop is hard to pick out. A rolling window is
        # also simply what an operations dashboard shows.
        span = timedelta(minutes=30)

        platforms = ["web", "ios", "android"]
        out: dict[str, list] = {p: [] for p in platforms}
        out["overall"] = []
        labels: list[str] = []

        async with w.sf() as sess:
            for i in range(buckets):
                b1 = start + step * (i + 1)
                b0 = b1 - span
                labels.append(b1.isoformat(timespec="minutes"))
                rows = (
                    await sess.execute(
                        select(
                            Session.platform,
                            func.count(Session.id),
                            func.sum(func.cast(Session.converted, Integer)),
                        )
                        .where(and_(Session.ts >= b0, Session.ts < b1))
                        .group_by(Session.platform)
                    )
                ).all()
                tot = sum(r[1] for r in rows)
                conv = sum((r[2] or 0) for r in rows)
                out["overall"].append(round(100.0 * conv / tot, 3) if tot else None)
                by = {r[0]: (r[1], r[2] or 0) for r in rows}
                for p in platforms:
                    n, c = by.get(p, (0, 0))
                    out[p].append(round(100.0 * c / n, 3) if n >= 150 else None)

        marks = [
            {
                "at": a.sim_time,
                "label": a.tool,
                "effective": a.verified_effective,
            }
            for a in s.mission.actions
        ]
        return {"labels": labels, "series": out, "actions": marks}


manager = MissionManager()
