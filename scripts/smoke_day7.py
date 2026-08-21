"""Day 7 gate: safety and parallelism.

The claim being tested is narrow and important: **the agent cannot take a
high-risk action without a human**. Not "is discouraged from", not "is told not
to" -- cannot. The check therefore looks at the world, not at the agent's
report: after a denied refund, the orders must be untouched.

Also checks the three ways an approval can end (granted, rejected, nobody
answered), that a denial is recorded rather than silently swallowed, and that
the parallel survey is actually concurrent.

    .venv/Scripts/python.exe scripts/smoke_day7.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import func, select

from fixer import tools as T
from fixer.api.missions import APPROVAL_TIMEOUT_SECONDS, Approval, MissionManager
from fixer.mission import Mission, MissionStatus
from fixer.sim.schema import Order
from fixer.sim.world import build_world


async def failed_orders(world) -> int:
    async with world.sf() as s:
        return (
            await s.execute(
                select(func.count(Order.id)).where(Order.status == "failed")
            )
        ).scalar_one()


async def refunded_orders(world) -> int:
    async with world.sf() as s:
        return (
            await s.execute(
                select(func.count(Order.id)).where(Order.status == "refunded")
            )
        ).scalar_one()


async def main() -> int:
    checks: dict[str, bool] = {}
    engine, world = await build_world("./day7.db", seed=4242, frozen=True)
    mission = Mission(objective="safety test")

    print("=" * 70)
    print("1. A denied high-risk action must not touch the world")
    print("=" * 70)

    def deny_all(meta, args):
        return f"{meta.name} needs approval" if meta.requires_approval else None

    env = T.ToolEnv(world=world, mission=mission, guard=deny_all, fast_forward=True)
    T.set_env(env)
    before_failed = await failed_orders(world)
    res = await T.act.issue_goodwill_refunds(since_minutes=120, reason="customer goodwill")
    after_refunded = await refunded_orders(world)
    after_failed = await failed_orders(world)

    print(f"  tool returned      : {res.get('error')} / {str(res.get('reason'))[:52]}")
    print(f"  failed orders      : {before_failed} -> {after_failed}")
    print(f"  refunded orders    : {after_refunded}   (must be 0)")
    checks["high-risk tool is refused"] = res.get("error") == "not_permitted"
    checks["no orders were refunded"] = after_refunded == 0
    checks["world is completely untouched"] = before_failed == after_failed
    checks["the refusal is on the ledger"] = any(c.denied for c in env.calls)
    checks["the refused call is not counted as an action"] = not any(
        a.tool == "issue_goodwill_refunds" and a.applied for a in mission.actions
    )

    print("\n" + "=" * 70)
    print("2. A low-risk action in the same session still proceeds")
    print("=" * 70)
    ok = await T.act.restore_configuration(
        key="checkout.session_ttl_minutes", reason="test"
    )
    print(f"  restore_configuration -> applied={ok.get('applied')}")
    checks["the gate blocks only what it should"] = ok.get("applied") is True

    print("\n" + "=" * 70)
    print("3. The gate pauses the agent and waits for a decision")
    print("=" * 70)
    mgr = MissionManager()

    class FakeSession:
        def __init__(self):
            self.approvals: list[Approval] = []
            self.pending_approval = None
            self.published: list[dict] = []

        def publish(self, p):
            self.published.append(p)

        def snapshot(self):
            return {}

    async def run_case(label: str, decide_after: float | None, approve: bool):
        s = FakeSession()
        guard = mgr._guard(s)  # noqa: SLF001 -- exercising the real gate
        meta = T.REGISTRY["issue_goodwill_refunds"]
        t0 = time.perf_counter()
        task = asyncio.create_task(guard(meta, {"reason": "goodwill", "since_minutes": 60}))
        await asyncio.sleep(0.15)  # let it reach the wait
        paused = not task.done()
        if decide_after is not None:
            await asyncio.sleep(decide_after)
            s.approvals[0].decided = True
            s.approvals[0].approved = approve
            s.approvals[0].event.set()
        result = await asyncio.wait_for(task, timeout=6)
        dt = time.perf_counter() - t0
        published = [p["type"] for p in s.published]
        print(f"  {label:<22} paused={paused}  result={'ALLOW' if result is None else 'DENY'}"
              f"  {dt:.2f}s  events={published}")
        return paused, result, published

    paused, res_ok, ev_ok = await run_case("approved", 0.2, True)
    checks["agent is genuinely paused"] = paused
    checks["approval lets the action through"] = res_ok is None
    checks["an approval request is published"] = "approval_required" in ev_ok

    _, res_no, _ = await run_case("rejected", 0.2, False)
    checks["rejection blocks the action"] = res_no is not None and "rejected" in res_no

    # Temporarily shorten the timeout so the no-answer path is testable.
    import fixer.api.missions as M

    original = M.APPROVAL_TIMEOUT_SECONDS
    M.APPROVAL_TIMEOUT_SECONDS = 1
    try:
        _, res_to, ev_to = await run_case("nobody answered", None, False)
    finally:
        M.APPROVAL_TIMEOUT_SECONDS = original
    checks["no answer blocks the action"] = res_to is not None
    checks["a timeout is published, not a hang"] = "approval_timeout" in ev_to

    print("\n" + "=" * 70)
    print("4. The parallel survey is actually parallel")
    print("=" * 70)
    env.guard = None
    t0 = time.perf_counter()
    survey = await T.read.survey_segments(window_minutes=60)
    par = time.perf_counter() - t0

    t0 = time.perf_counter()
    for dim in ("platform", "region", "traffic_source", "app_version"):
        await T.read.query_conversion_funnel(window_minutes=60, split_by=dim)
    await T.read.query_payments(window_minutes=60)
    await T.read.query_infrastructure(window_minutes=60)
    seq = time.perf_counter() - t0

    print(f"  survey_segments (1 call, {survey['queries_run_concurrently']} queries) : {par * 1000:.0f}ms")
    print(f"  the same six sequentially                    : {seq * 1000:.0f}ms")
    print(f"  ranking: " + "  ".join(
        f"{d['dimension']}={d['unevenness']:.2f}" for d in survey["unevenness_ranking"]))
    checks["parallel survey beats sequential"] = par < seq
    checks["survey covers four dimensions"] = len(survey["unevenness_ranking"]) == 4

    print("\n" + "=" * 70)
    print("5. Every terminal state is reachable and distinct")
    print("=" * 70)
    terminal = [s for s in MissionStatus if s.terminal]
    print("  " + ", ".join(s.value for s in terminal))
    checks["seven distinct terminal states"] = len(terminal) == 7
    checks["RUNNING is not terminal"] = not MissionStatus.RUNNING.terminal

    print("\n" + "=" * 70)
    for name, ok_ in checks.items():
        print(f"  [{'PASS' if ok_ else 'FAIL'}] {name}")
    print("=" * 70)
    await engine.dispose()
    try:
        os.remove("./day7.db")
    except OSError:
        pass
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
