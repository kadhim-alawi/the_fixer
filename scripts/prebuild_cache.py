"""Generate the NovaCart history caches ahead of time.

Run during the container build so the deployed service does not spend forty
seconds generating a day of history the first time somebody clicks Start
Mission. A judge's first impression should not be a progress bar.

    .venv/Scripts/python.exe scripts/prebuild_cache.py --seeds 4242
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from fixer.sim import incidents as I
from fixer.sim.world import CACHE_DIR, build_world


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="4242", help="comma-separated")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    scratch = Path(os.environ.get("FIXER_MISSION_DIR", "./missions"))
    scratch.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    for seed in seeds:
        for key in I.ALL_KEYS:
            tmp = scratch / f"prebuild_{key}_{seed}.db"
            t = time.perf_counter()
            engine, _world = await build_world(str(tmp), incident_key=key, seed=seed)
            await engine.dispose()
            tmp.unlink(missing_ok=True)
            print(f"  {key:<28} seed={seed}  {time.perf_counter() - t:5.1f}s")

    files = sorted(CACHE_DIR.glob("*.db"))
    total = sum(f.stat().st_size for f in files) / 1e6
    print(f"\n{len(files)} cache files, {total:.1f} MB, {time.perf_counter() - t0:.1f}s total")
    for f in files:
        print(f"  {f.name}  {f.stat().st_size / 1e6:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
