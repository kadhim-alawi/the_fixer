"""Capture the approval dialog on the live service, to check it reads correctly."""
from __future__ import annotations
import asyncio, sys
from pathlib import Path
from playwright.async_api import async_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
OUT = Path("docs/shots")

async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1600, "height": 950})
        await page.goto(URL, wait_until="networkidle")
        await page.click("button.primary")
        try:
            await page.wait_for_selector(".scrim", timeout=600_000)
            await asyncio.sleep(1.5)
            await page.screenshot(path=OUT / "04-approval.png")
            ctx = await page.locator(".modal .ctx").inner_text()
            print("context line:", ctx.encode("ascii", "replace").decode())
            print("shot saved")
        except Exception as e:
            await page.screenshot(path=OUT / "04-no-approval.png")
            print("no approval appeared this run:", type(e).__name__)
        await b.close()

asyncio.run(main())
