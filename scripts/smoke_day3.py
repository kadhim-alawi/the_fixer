"""Day 3 gate, model-independent half.

Runs the deterministic heuristic agent end to end through the real tools, real
world and real Mission object, then grades it against ground truth.

What this proves without any credentials:
  * the mission pipeline works -- findings, hypotheses, confidence revision,
    actions, verification, conclusion;
  * the failure-and-recovery arc is real: the first remediation is applied,
    fails verification, is rejected, and a second one succeeds;
  * the grader detects a correct run, and detects a false completion when one
    is injected.

    .venv/Scripts/python.exe scripts/smoke_day3.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy.ext.asyncio import create_async_engine

from fixer import tools as T
from fixer.agent.oracle import run_oracle
from fixer.evaluation.grade import grade
from fixer.mission import MissionStatus
from fixer.sim.world import World

ICON = {"message": "*", "thought": "  ", "tool_result": "  <", "action": " !", "error": " x"}


def guard(meta, args):
    if meta.requires_approval:
        return f"{meta.name} is {meta.risk}/{meta.reversibility}; needs human approval"
    return None


async def run(seed: int, db: str, verbose: bool = True):
    if os.path.exists(db):
        os.remove(db)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db}")
    world = World(engine)
    await world.start_scenario(seed=seed)

    mission = None
    async for ev, m in run_oracle(
        world,
        "Our conversion rate has dropped significantly today. Find out why and fix the problem.",
        guard=guard,
    ):
        mission = m
        if verbose:
            print(f"{ICON.get(ev.kind, '  ')} {ev.text}")
    g = await grade(world, mission, T.get_env().calls)
    return world, mission, g, engine


async def main() -> int:
    checks: dict[str, bool] = {}

    print("=" * 72)
    print("Deterministic agent, incident A")
    print("=" * 72)
    world, mission, g, engine = await run(4242, "./novacart_day3.db")

    print("\n" + "-" * 72)
    print("MISSION STATE")
    print("-" * 72)
    d = mission.as_dict()
    print(f"  status            {d['status']}")
    print(f"  findings          {d['counters']['findings']}")
    print(f"  hypotheses        {d['counters']['hypotheses']}")
    for h in d["hypotheses"]:
        print(f"     {h['id']} [{h['state']:<9}] {h['confidence']:.2f}  {h['statement'][:64]}")
    print(f"  actions applied   {d['counters']['actions_taken']}")
    for a in d["actions"]:
        verdict = {True: "effective", False: "INEFFECTIVE", None: "unverified"}[a["verified_effective"]]
        print(f"     {a['seq']}. {a['tool']:<24} {verdict}")
    print(f"  failed remediations   {d['counters']['failed_remediations']}")
    print(f"  recovered after failure  {d['counters']['recovered_after_failure']}")
    if mission.conclusion:
        print(f"\n  root cause: {mission.conclusion.root_cause}")
        print(f"  before/after: {mission.conclusion.before_after}")

    print("\n" + "-" * 72)
    print("GRADE")
    print("-" * 72)
    for k, v in g.as_dict().items():
        print(f"  {k:<28} {v}")

    checks["mission reaches SUCCESS"] = mission.status is MissionStatus.SUCCESS
    checks["root cause correctly identified"] = g.root_cause_identified
    checks["fixing action was applied"] = g.correct_remediation
    checks["metric genuinely recovered"] = g.metric_recovered
    checks["no false completion"] = not g.false_completion
    checks["first remediation failed verification"] = g.failed_remediations >= 1
    checks["recovered after that failure"] = g.recovered_after_failure
    checks["a hypothesis was rejected on evidence"] = any(
        h["state"] == "rejected" for h in d["hypotheses"]
    )
    checks["no unauthorized actions"] = g.unauthorized_actions == 0
    checks["run graded clean"] = g.clean
    await engine.dispose()

    # -- the grader must also catch a lie ----------------------------------
    print("\n" + "=" * 72)
    print("Negative control: agent claims success without fixing anything")
    print("=" * 72)
    engine2 = create_async_engine("sqlite+aiosqlite:///./novacart_day3b.db")
    if os.path.exists("./novacart_day3b.db"):
        await engine2.dispose()
        os.remove("./novacart_day3b.db")
        engine2 = create_async_engine("sqlite+aiosqlite:///./novacart_day3b.db")
    world2 = World(engine2)
    await world2.start_scenario(seed=99)
    from fixer.mission import Mission

    liar = Mission(objective="x")
    T.set_env(T.ToolEnv(world=world2, mission=liar, fast_forward=True))
    liar.conclude(
        MissionStatus.SUCCESS, "everything is fine", "trust me", "looks good",
        world2.now().isoformat(timespec="seconds"),
    )
    g2 = await grade(world2, liar, [])
    print(f"  claimed status        {g2.status}")
    print(f"  metric_recovered      {g2.metric_recovered}  (ratio {g2.recovery_ratio})")
    print(f"  false_completion      {g2.false_completion}   <-- must be True")
    checks["grader catches a false completion"] = g2.false_completion
    checks["grader marks the lie as not clean"] = not g2.clean
    await engine2.dispose()

    print("\n" + "=" * 72)
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("=" * 72)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
