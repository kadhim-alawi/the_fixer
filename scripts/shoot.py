"""Screenshot Mission Control mid-mission and at the end.

Used to check the console actually reads well, and to produce stills for the
submission. Runs a real mission against a real server -- nothing here is mocked.

    .venv/Scripts/python.exe scripts/shoot.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
OUT = Path("docs/shots")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1600, "height": 950})
        errors: list[str] = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        await page.goto(URL, wait_until="networkidle")
        await page.screenshot(path=OUT / "01-idle.png")
        print("idle shot taken")

        await page.click("button.primary")
        # The world takes a moment to build, then events start arriving.
        await page.wait_for_selector(".row", timeout=120_000)
        await asyncio.sleep(12)
        await page.screenshot(path=OUT / "02-investigating.png")
        print("investigating shot taken")

        # Wait for the mission to conclude, or give up and shoot anyway.
        try:
            await page.wait_for_selector(".conclusion", timeout=240_000)
            await asyncio.sleep(2)
        except Exception:
            print("no conclusion within timeout; shooting current state")
        await page.screenshot(path=OUT / "03-complete.png")
        print("final shot taken")

        rows = await page.locator(".row").count()
        hyps = await page.locator(".hyp").count()
        acts = await page.locator(".act").count()
        concl = await page.locator(".conclusion h3").inner_text() if await page.locator(".conclusion").count() else "(none)"
        print(f"\ntimeline rows={rows}  hypotheses={hyps}  remediations={acts}")
        print("conclusion:", concl.encode("ascii", "replace").decode())
        if errors:
            print("\nCONSOLE ERRORS:")
            for e in errors[:10]:
                print("  ", e)
        else:
            print("no console errors")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
