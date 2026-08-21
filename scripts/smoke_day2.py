"""Day 2 gate.

Checks the tool layer without needing model credentials:

1. every tool produces a valid ADK function declaration -- this is what the
   model plans against, and a bad signature or docstring only shows up at
   runtime otherwise;
2. every tool executes against a live scenario and returns real numbers;
3. the ledger records each call with its safety metadata;
4. the safety gate can refuse a high-risk tool before it runs;
5. no read tool leaks the incident.

    .venv/Scripts/python.exe scripts/smoke_day2.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from sqlalchemy.ext.asyncio import create_async_engine

from fixer import tools as T
from fixer.sim.world import World

DB = "sqlite+aiosqlite:///./novacart_day2.db"
BANNED = ["legacy_v2 was set by the incident", "incident", "root_cause", "resolved_at"]


def head(title: str) -> None:
    print(f"\n{'-' * 66}\n{title}\n{'-' * 66}")


def brief(obj, width: int = 300) -> str:
    s = json.dumps(obj, default=str, separators=(",", ":"))
    return s if len(s) <= width else s[:width] + " ..."


async def main() -> int:
    checks: dict[str, bool] = {}

    # -- 1. ADK schema generation ------------------------------------------
    head("1. ADK function declarations")
    from google.adk.tools import FunctionTool

    bad: list[str] = []
    for fn in T.ALL_TOOLS:
        try:
            decl = FunctionTool(fn)._get_declaration()
            if decl is None or not decl.name:
                bad.append(f"{fn.__name__}: no declaration")
                continue
            # ADK 2.x emits JSON Schema; older builds used the `parameters` field.
            schema = getattr(decl, "parameters_json_schema", None)
            props = (schema or {}).get("properties") if schema else (
                decl.parameters.properties if decl.parameters else None
            )
            if not props:
                bad.append(f"{fn.__name__}: declaration exposes no parameters")
                continue
            if not (decl.description or "").strip():
                bad.append(f"{fn.__name__}: no description for the model to plan against")
                continue
            print(f"  ok  {decl.name:<26} params={len(props):<2} "
                  f"({', '.join(list(props)[:4])}{' ...' if len(props) > 4 else ''})")
        except Exception as exc:
            bad.append(f"{fn.__name__}: {type(exc).__name__}: {exc}")
    for b in bad:
        print(f"  FAIL {b}")
    checks["every tool produces an ADK declaration"] = not bad
    checks["tool count matches the registry"] = len(T.ALL_TOOLS) == len(T.REGISTRY) == 25

    # -- 2. execute against a live world -----------------------------------
    head("2. Live execution against NovaCart")
    if os.path.exists("novacart_day2.db"):
        os.remove("novacart_day2.db")
    engine = create_async_engine(DB)
    world = World(engine)
    await world.start_scenario(seed=7331)
    env = T.ToolEnv(world=world)
    T.set_env(env)

    r = await T.read.query_conversion_funnel(window_minutes=60, split_by="platform")
    for seg in r["segments"]:
        print(f"  {seg['platform']:<8} conv={seg['conversion_rate_pct']}%  "
              f"sessions={seg['sessions']:,}  checkout_completion={seg['checkout_completion_pct']}%")
    ios = next(s for s in r["segments"] if s["platform"] == "ios")
    web = next(s for s in r["segments"] if s["platform"] == "web")
    checks["funnel isolates the affected platform"] = ios["conversion_rate_pct"] < web["conversion_rate_pct"] / 2

    p = await T.read.query_payments(window_minutes=60, split_by="platform")
    print(f"\n  payment failures by code: {brief(p['failures_by_error_code'], 200)}")
    codes = [c["error_code"] for c in p["failures_by_error_code"]]
    checks["incident error code is discoverable"] = "PAY_CFG_3021" in codes
    checks["ordinary declines still present"] = "CARD_DECLINED" in codes

    lg = await T.read.query_logs(window_minutes=60, level="ERROR", limit=2)
    print(f"  log sample: {brief(lg['samples'][:1], 260)}")

    cfg = await T.read.query_configuration(changed_within_hours=24)
    changed = [c for c in cfg["entries"] if c["changed"]]
    print(f"\n  config keys changed in last 24h: {len(changed)}")
    for c in changed[:6]:
        print(f"    {c['key']:<42} {c['previous_value']} -> {c['value']}  by {c['updated_by']}")
    checks["config churn hides the real change"] = len(changed) >= 6

    dep = await T.read.query_deployments(hours_back=6)
    print(f"\n  deployments in last 6h: {len(dep['deployments'])}")
    for d in dep["deployments"][:3]:
        print(f"    #{d['ref']} {d['service']:<14} {d['minutes_ago']}m ago  {d['summary'][:52]}")

    inf = await T.read.query_infrastructure(window_minutes=60)
    print(f"\n  infra: {brief([(s['service'], s['error_rate_mean'], s['latency_p95_mean_ms']) for s in inf['services']], 260)}")

    tk = await T.read.query_support_tickets(window_minutes=180, limit=3)
    print(f"  tickets by platform: {brief(tk['counts_by_platform'], 160)}")

    await T.read.query_orders(window_minutes=60)
    await T.read.query_feature_flags(changed_within_hours=48)

    head("3. Verification tools (no verdict, just numbers)")
    cv = await T.verify.check_conversion(window_minutes=30, platform="ios")
    print(f"  ios  now={cv['current']['rate_pct']}%  24h_ago={cv['reference_24h_ago']['rate_pct']}%  "
          f"change={cv['change']['relative_pct']}%  sufficient_sample={cv['sufficient_sample']}")
    ps = await T.verify.check_payment_success(window_minutes=30, platform="ios")
    print(f"  ios payment success now={ps['current']['rate_pct']}%  24h_ago={ps['reference_24h_ago']['rate_pct']}%")
    er = await T.verify.check_error_rate(window_minutes=30, error_code="PAY_CFG_3021")
    print(f"  PAY_CFG_3021 now={er['current_count']}  24h_ago={er['reference_24h_ago_count']}")
    checks["verifier returns no solved/verdict field"] = not any(
        k in json.dumps(cv).lower() for k in ("solved", "verdict", "root_cause")
    )

    # -- 4. safety gate -----------------------------------------------------
    head("4. Safety gate")
    def guard(meta: T.ToolMeta, args: dict) -> str | None:
        if meta.requires_approval:
            return f"{meta.name} is {meta.risk}/{meta.reversibility}; needs human approval"
        return None

    env.guard = guard
    blocked = await T.act.issue_goodwill_refunds(since_minutes=60, reason="test")
    print(f"  issue_goodwill_refunds -> {brief(blocked, 180)}")
    allowed = await T.act.restore_configuration(key="checkout.session_ttl_minutes", reason="test")
    print(f"  restore_configuration  -> {brief(allowed, 180)}")
    checks["high-risk tool is refused by the gate"] = blocked.get("error") == "not_permitted"
    checks["low-risk tool passes the gate"] = allowed.get("applied") is True
    env.guard = None

    # -- 5. ledger and leak check ------------------------------------------
    head("5. Evidence ledger")
    print(f"  calls recorded: {len(env.calls)}")
    for c in env.calls[:3]:
        print(f"    #{c.seq} {c.name:<26} {c.meta.permission}/{c.meta.risk:<6} {c.duration_ms}ms")
    denied = [c for c in env.calls if c.denied]
    print(f"  denied calls recorded: {len(denied)}")
    checks["every call is on the ledger"] = len(env.calls) == 14
    checks["denied calls are recorded too"] = len(denied) == 1
    checks["every call carries safety metadata"] = all(c.meta is not None for c in env.calls)

    blob = json.dumps([c.result for c in env.calls], default=str).lower()
    leaks = [w for w in ("incident_start", "resolved_at", "root_cause", "is_fixed_by") if w in blob]
    print(f"  leak scan: {'clean' if not leaks else leaks}")
    checks["no tool result leaks scenario internals"] = not leaks

    # -- summary ------------------------------------------------------------
    print("\n" + "=" * 66)
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    print("=" * 66)
    await engine.dispose()
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
