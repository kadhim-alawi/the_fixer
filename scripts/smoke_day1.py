"""Day 1 gate.

Proves the simulator behaves the way the whole project depends on:

1. a scenario starts and conversion is genuinely depressed on one platform only;
2. the plausible-but-wrong remediation (roll back the correlated deployment)
   really is applied, and conversion really does *not* recover;
3. the correct remediation (restore the config) makes conversion recover.

Step 2 is the one that matters. The demo's signature moment is a real failure,
not a scripted one, and this is where that gets verified.

    uv run --python .venv python scripts/smoke_day1.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from fixer.sim.incidents import Action
from fixer.sim.schema import Payment, Session
from fixer.sim.world import World

DB = "sqlite+aiosqlite:///./novacart_smoke.db"


async def conversion(world: World, minutes: int, platform: str | None = None) -> float:
    """Conversion rate over the last N sim minutes, optionally one platform."""
    end = world.now()
    start = end - timedelta(minutes=minutes)
    q = select(
        func.count(Session.id),
        func.sum(func.cast(Session.converted, __import__("sqlalchemy").Integer)),
    ).where(Session.ts >= start, Session.ts < end)
    if platform:
        q = q.where(Session.platform == platform)
    async with world.sf() as s:
        total, conv = (await s.execute(q)).one()
    return 100.0 * (conv or 0) / total if total else 0.0


async def error_count(world: World, minutes: int, code: str) -> int:
    end = world.now()
    start = end - timedelta(minutes=minutes)
    async with world.sf() as s:
        n = (
            await s.execute(
                select(func.count(Payment.id)).where(
                    Payment.ts >= start, Payment.ts < end, Payment.error_code == code
                )
            )
        ).scalar_one()
    return n


def line(label: str, value: str) -> None:
    print(f"  {label:<34} {value}")


async def main() -> int:
    if os.path.exists("novacart_smoke.db"):
        os.remove("novacart_smoke.db")
    engine = create_async_engine(DB)
    world = World(engine)

    print("\n[1] Starting scenario (incident hidden from the agent)")
    sc = await world.start_scenario(seed=4242)
    line("scenario", sc.scenario_id)
    line("sim time now", str(sc.sim_start))
    line("rows generated through", str(sc.generated_to))

    async with world.sf() as s:
        n_sessions = (await s.execute(select(func.count(Session.id)))).scalar_one()
        n_payments = (await s.execute(select(func.count(Payment.id)))).scalar_one()
    line("sessions in warehouse", f"{n_sessions:,}")
    line("payment attempts", f"{n_payments:,}")

    print("\n[2] Baseline vs. now")
    # Baseline = a window well before the incident started.
    world.advance(-6 * 60)  # look back to well before the incident
    base_all = await conversion(world, 180)
    base_ios = await conversion(world, 180, "ios")
    world.advance(6 * 60)  # back to the present
    now_all = await conversion(world, 60)
    now_ios = await conversion(world, 60, "ios")
    now_web = await conversion(world, 60, "web")
    now_and = await conversion(world, 60, "android")
    line("conversion 6h ago (all)", f"{base_all:.2f}%")
    line("conversion now (all)", f"{now_all:.2f}%  <-- the visible symptom")
    line("  ios  6h ago -> now", f"{base_ios:.2f}%  ->  {now_ios:.2f}%")
    line("  web  now", f"{now_web:.2f}%")
    line("  android now", f"{now_and:.2f}%")
    line("PAY_CFG_3021 in last 60m", str(await error_count(world, 60, "PAY_CFG_3021")))

    ok_symptom = now_ios < base_ios * 0.6 and now_web > 2.5
    print(f"  => platform-isolated drop present: {ok_symptom}")

    print("\n[3] Wrong-but-reasonable remediation: roll back deployment 8472")
    res = await world.apply_action(Action("rollback_deployment", "8472"))
    line("tool result", str(res.get("applied")))
    line("tool said", res.get("detail", ""))
    world.advance(70)
    await world.tick()
    after_bad_ios = await conversion(world, 60, "ios")
    after_bad_all = await conversion(world, 60)
    line("ios conversion after rollback", f"{after_bad_ios:.2f}%")
    line("overall after rollback", f"{after_bad_all:.2f}%")
    ok_no_recovery = after_bad_ios < base_ios * 0.6
    print(f"  => tool succeeded but problem persists: {ok_no_recovery}")

    print("\n[4] Correct remediation: restore payments.ios.provider_profile")
    res = await world.apply_action(
        Action("restore_configuration", "payments.ios.provider_profile")
    )
    line("tool said", res.get("detail", ""))
    world.advance(70)
    await world.tick()
    after_fix_ios = await conversion(world, 60, "ios")
    after_fix_all = await conversion(world, 60)
    line("ios conversion after fix", f"{after_fix_ios:.2f}%")
    line("overall after fix", f"{after_fix_all:.2f}%")
    line("PAY_CFG_3021 in last 60m", str(await error_count(world, 60, "PAY_CFG_3021")))
    # How far back toward baseline did we actually come? Comparing against an
    # arbitrary absolute threshold would hide partial recoveries.
    recovered = (after_fix_ios - now_ios) / (base_ios - now_ios) if base_ios > now_ios else 0.0
    line("recovery toward baseline", f"{recovered * 100:.0f}%")
    ok_recovery = recovered >= 0.85

    print("\n" + "=" * 62)
    checks = {
        "symptom is platform-isolated": ok_symptom,
        "wrong fix does NOT recover metric": ok_no_recovery,
        "correct fix DOES recover metric": ok_recovery,
    }
    for name, passed in checks.items():
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
    print("=" * 62)

    await engine.dispose()
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
