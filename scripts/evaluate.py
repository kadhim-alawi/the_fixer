"""Run a batch of missions and report what actually happened.

    .venv/Scripts/python.exe scripts/evaluate.py --agent oracle --runs 3
    .venv/Scripts/python.exe scripts/evaluate.py --agent llm --runs 2

The heuristic agent needs no credentials and is the baseline. Reporting the
LLM's numbers alongside it is what turns "solved 5 of 6" into a claim about
reasoning rather than about the environment being easy.

Results are written to eval_results.json so the README quotes measured numbers
rather than hoped-for ones.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from sqlalchemy.ext.asyncio import create_async_engine

from fixer import tools as T
from fixer.evaluation.grade import grade, summarise
from fixer.sim import incidents as I
from fixer.sim.world import World, build_world

OBJECTIVE = (
    "Our conversion rate has dropped significantly today. "
    "Find out why and fix the problem."
)


def guard(meta, args):
    if meta.requires_approval:
        return (
            f"{meta.name} is {meta.risk} risk and {meta.reversibility}; a human "
            "operator must approve it. Continue without it."
        )
    return None


async def one_run(agent: str, incident_key: str, seed: int) -> dict:
    db = f"./eval_{incident_key}_{seed}.db"
    if os.path.exists(db):
        os.remove(db)
    # Frozen clock: the world must depend on the seed, not on how long the
    # agent took to think. Otherwise a slow LLM and a fast heuristic are not
    # being scored on the same scenario.
    engine, world = await build_world(db, incident_key=incident_key, seed=seed, frozen=True)

    t0 = time.perf_counter()
    if agent == "oracle":
        from fixer.agent.oracle import run_oracle

        mission = None
        async for _ev, m in run_oracle(world, OBJECTIVE, fast_forward=True, guard=guard):
            mission = m
    else:
        from fixer.agent.llm import run_llm_mission

        mission = await run_llm_mission(world, OBJECTIVE, fast_forward=True, guard=guard)

    wall = time.perf_counter() - t0
    g = await grade(world, mission, T.get_env().calls)
    await engine.dispose()
    try:
        os.remove(db)
    except OSError:
        pass
    d = g.as_dict()
    d["seed"] = seed
    d["wall_seconds"] = round(wall, 1)
    return d


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", choices=["oracle", "llm"], default="oracle")
    ap.add_argument("--runs", type=int, default=1, help="runs per incident")
    ap.add_argument("--incidents", default="all")
    ap.add_argument("--out", default="eval_results.json")
    args = ap.parse_args()

    keys = I.ALL_KEYS if args.incidents == "all" else args.incidents.split(",")
    grades = []

    print(f"\nagent={args.agent}  incidents={len(keys)}  runs_each={args.runs}\n")
    header = f"  {'incident':<28}{'seed':>6}{'status':>10}{'cause':>7}{'fix':>5}{'recov':>7}{'false':>7}{'calls':>7}{'secs':>7}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for key in keys:
        for i in range(args.runs):
            seed = 1000 + i * 137
            d = await one_run(args.agent, key, seed)
            grades.append(d)
            print(
                f"  {key:<28}{seed:>6}{d['status']:>10}"
                f"{'Y' if d['root_cause_identified'] else '.':>7}"
                f"{'Y' if d['correct_remediation'] else '.':>5}"
                f"{'Y' if d['metric_recovered'] else '.':>7}"
                f"{'!!' if d['false_completion'] else '.':>7}"
                f"{d['tool_calls']:>7}{d['wall_seconds']:>7}"
            )

    from fixer.evaluation.grade import Grade

    summary = summarise([Grade(**{k: v for k, v in g.items() if k in Grade.__annotations__}) for g in grades])

    print("\n" + "=" * 60)
    print(f"SUMMARY  ({args.agent})")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k:<42} {v}")
    print("=" * 60)

    payload = {
        "agent": args.agent,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds"),
        "summary": summary,
        "runs": grades,
    }
    existing = {}
    if os.path.exists(args.out):
        try:
            existing = json.load(open(args.out))
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing[args.agent] = payload
    json.dump(existing, open(args.out, "w"), indent=2)
    print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
