"""Run one real mission against NovaCart and print the live timeline.

    .venv/Scripts/python.exe scripts/run_mission.py
    .venv/Scripts/python.exe scripts/run_mission.py --seed 4242 --speed 120

Needs model credentials. Either put them in a .env file at the repo root:

    GOOGLE_API_KEY=...

or, for Vertex AI:

    GOOGLE_GENAI_USE_VERTEXAI=1
    GOOGLE_CLOUD_PROJECT=your-project
    GOOGLE_CLOUD_LOCATION=us-central1
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from sqlalchemy.ext.asyncio import create_async_engine

from fixer.agent import model as model_cfg
from fixer.agent.runner import stream_mission
from fixer.sim.world import build_world

OBJECTIVE = (
    "Our conversion rate has dropped significantly today. "
    "Find out why and fix the problem."
)

ICON = {
    "message": "*",
    "thought": " ",
    "tool_call": "?",
    "tool_result": "<",
    "action": "!",
    "error": "x",
}


def approval_guard(meta, args):
    """Day 2 placeholder for the Day 7 approval flow: refuse, with a reason."""
    if meta.requires_approval:
        return (
            f"{meta.name} is {meta.risk} risk and {meta.reversibility}. "
            "A human operator must approve it. Continue without it."
        )
    return None


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--speed", type=float, default=120.0, help="sim seconds per real second")
    ap.add_argument("--objective", default=OBJECTIVE)
    ap.add_argument("--incident", default="payment_config_regression")
    ap.add_argument("--db", default="./novacart_mission.db")
    args = ap.parse_args()

    backend = model_cfg.resolve()
    print(f"\nmodel backend : {backend.kind}")
    print(f"                {backend.detail}")
    if not backend.ready:
        print("\nNo model credentials configured, so the mission cannot run.")
        print("Everything else is built and tested -- see scripts/smoke_day2.py.")
        print(__doc__.split("Needs model credentials.")[1])
        return 2

    print(f"model         : {backend.model}")

    if os.path.exists(args.db):
        os.remove(args.db)
    engine = create_async_engine(f"sqlite+aiosqlite:///{args.db}")
    world = World(engine)

    print("\nbuilding NovaCart ...", end="", flush=True)
    sc = await world.start_scenario(seed=args.seed, speed=args.speed)
    print(f" done  (scenario {sc.scenario_id}, {args.speed:g}x sim clock)")
    print(f"\nOBJECTIVE: {args.objective}\n")
    print("-" * 78)

    async for ev in stream_mission(world, args.objective, guard=approval_guard):
        icon = ICON.get(ev.kind, " ")
        if ev.kind in ("tool_call", "action"):
            tag = f"[{ev.risk}]" if ev.risk and ev.risk != "LOW" else ""
            print(f" {icon} {ev.text} {tag}")
        elif ev.kind == "tool_result":
            print(f"   {icon} {ev.text}")
        elif ev.kind == "thought":
            for line in ev.text.splitlines():
                print(f"     {line}")
        else:
            print(f" {icon} {ev.text}")

    print("-" * 78)
    await engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
