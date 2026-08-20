"""Day 5 gate: validate the incident library.

For every incident, mechanically check the three properties the evaluation
depends on:

1. **The symptom is real.** Conversion is measurably down against yesterday.
2. **The decoy genuinely fails.** The most plausible wrong action is applied
   for real and the metric does not recover. This is what makes the demo's
   failure moment honest rather than staged.
3. **The fix genuinely works.** The correct action recovers the metric.

It also prints each incident's discriminating signature, so it is visible at a
glance that the six are told apart by different evidence -- platform, region,
funnel stage, error code, infrastructure -- rather than all looking the same.

    .venv/Scripts/python.exe scripts/validate_incidents.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy.ext.asyncio import create_async_engine

from fixer import tools as T
from fixer.evaluation.grade import _affected_segments, _conversion
from fixer.sim import incidents as I
from fixer.sim.world import World, build_world

SETTLE = 50
WINDOW = 45


async def worst_segment(world, inc) -> tuple[float, float]:
    """Conversion now vs baseline for the segment this incident hits hardest.

    Judging on the aggregate hides a severe problem confined to one slice --
    Android is 18% of traffic, so a total Android outage moves the overall
    number by less than a fifth. The grader uses the same rule.
    """
    worst = (1e9, 0.0, 0.0)
    for _label, dim, val in _affected_segments(inc):
        cur_n, cur_c = await _conversion(world, WINDOW, 0, dim, val)
        ref_n, ref_c = await _conversion(world, 180, 24 * 60, dim, val)
        if min(cur_n, ref_n) < 150:
            continue
        cur = 100.0 * cur_c / cur_n
        ref = 100.0 * ref_c / ref_n
        if ref and cur / ref < worst[0]:
            worst = (cur / ref, cur, ref)
    return worst[1], worst[2]


async def signature(inc) -> dict:
    """The evidence that distinguishes this incident from the others."""
    plat = await T.read.query_conversion_funnel(window_minutes=60, split_by="platform")
    region = await T.read.query_conversion_funnel(window_minutes=60, split_by="region")
    pay = await T.read.query_payments(window_minutes=60)
    infra = await T.read.query_infrastructure(window_minutes=60)

    def spread(segments):
        rates = [(s.get("platform") or s.get("region"), s["conversion_rate_pct"] or 0)
                 for s in segments if s["sessions"] > 250]
        if not rates:
            return "-", 0.0
        lo = min(rates, key=lambda r: r[1])
        avg = sum(r[1] for r in rates) / len(rates)
        return lo[0], (1 - lo[1] / avg) if avg else 0.0

    worst_p, spread_p = spread(plat.get("segments", []))
    worst_r, spread_r = spread(region.get("segments", []))
    ordinary = {"CARD_DECLINED", "INSUFFICIENT_FUNDS", "EXPIRED_CARD", "RISK_HOLD"}
    unusual = [c for c in pay["failures_by_error_code"]
               if c["error_code"] not in ordinary and c["count"] > 5]
    worst_svc = max(infra["services"], key=lambda s: s["error_rate_mean"])
    return {
        "platform": f"{worst_p} (-{spread_p * 100:.0f}%)" if spread_p > 0.25 else "even",
        "region": f"{worst_r} (-{spread_r * 100:.0f}%)" if spread_r > 0.25 else "even",
        "error_code": unusual[0]["error_code"] if unusual else "none",
        "worst_service": f"{worst_svc['service']} {worst_svc['error_rate_mean']:.3f} "
                         f"{worst_svc['latency_p95_mean_ms']}ms",
    }


async def check(key: str) -> dict:
    inc = I.get(key)
    db = f"./val_{key}.db"
    if os.path.exists(db):
        os.remove(db)
    engine, world = await build_world(db, incident_key=key, seed=1234, frozen=True)
    T.set_env(T.ToolEnv(world=world, fast_forward=True))

    cur, ref = await worst_segment(world, inc)
    symptom = ref > 0 and cur < ref * 0.88
    sym_level = cur
    sig = await signature(inc)

    # The plausible wrong move.
    await world.apply_action(inc.reference_decoy)
    await T.verify.wait_for_traffic(minutes=SETTLE)
    d_cur, d_ref = await worst_segment(world, inc)
    # Recovered less than 40% of the drop means the decoy did nothing useful.
    decoy_failed = d_ref > sym_level and (d_cur - sym_level) / (d_ref - sym_level) < 0.40

    # The right move.
    await world.apply_action(inc.reference_fix)
    await T.verify.wait_for_traffic(minutes=SETTLE)
    f_cur, f_ref = await worst_segment(world, inc)
    fix_worked = f_ref > sym_level and (f_cur - sym_level) / (f_ref - sym_level) >= 0.70

    await engine.dispose()
    os.remove(db)
    return {
        "key": key,
        "baseline": ref, "symptom": cur,
        "after_decoy": d_cur, "after_fix": f_cur,
        "symptom_real": symptom,
        "decoy_failed": decoy_failed,
        "fix_worked": fix_worked,
        "fix_action": f"{inc.reference_fix.kind}({inc.reference_fix.target})",
        "decoy_action": f"{inc.reference_decoy.kind}({inc.reference_decoy.target})",
        **sig,
    }


async def main() -> int:
    rows = []
    for key in I.ALL_KEYS:
        print(f"  running {key} ...", flush=True)
        rows.append(await check(key))

    print("\n" + "=" * 108)
    print("INCIDENT SIGNATURES -- what tells each one apart")
    print("=" * 108)
    print(f"  {'incident':<28}{'platform':<18}{'region':<16}{'error code':<18}{'worst service'}")
    print("  " + "-" * 104)
    for r in rows:
        print(f"  {r['key']:<28}{r['platform']:<18}{r['region']:<16}{r['error_code']:<18}{r['worst_service']}")

    print("\n" + "=" * 108)
    print("CAUSALITY -- the decoy must fail and the fix must work")
    print("=" * 108)
    print(f"  {'incident':<28}{'base':>7}{'symptom':>9}{'+decoy':>8}{'+fix':>8}   {'decoy applied':<38}{'fix applied'}")
    print("  " + "-" * 104)
    for r in rows:
        print(f"  {r['key']:<28}{r['baseline']:>6.2f}%{r['symptom']:>8.2f}%"
              f"{r['after_decoy']:>7.2f}%{r['after_fix']:>7.2f}%   "
              f"{r['decoy_action']:<38}{r['fix_action']}")

    checks = {}
    for r in rows:
        checks[f"{r['key']}: symptom is real"] = r["symptom_real"]
        checks[f"{r['key']}: decoy does not fix it"] = r["decoy_failed"]
        checks[f"{r['key']}: correct fix recovers it"] = r["fix_worked"]

    fix_kinds = {r["fix_action"].split("(")[0] for r in rows}
    checks["fixes span at least 4 different action types"] = len(fix_kinds) >= 4
    sigs = {(r["platform"], r["region"], r["error_code"]) for r in rows}
    checks["every incident has a distinct signature"] = len(sigs) == len(rows)

    print("\n" + "=" * 108)
    failed = [k for k, v in checks.items() if not v]
    for k, v in checks.items():
        if not v:
            print(f"  [FAIL] {k}")
    print(f"  {len(checks) - len(failed)}/{len(checks)} checks passed")
    print(f"  distinct fix action types: {sorted(fix_kinds)}")
    print("=" * 108)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
