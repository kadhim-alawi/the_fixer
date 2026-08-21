"""Render the architecture diagram to PNG for the Devpost upload."""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

SRC = Path("docs/architecture.html").resolve()
OUT = Path("docs/architecture.png")


async def main() -> None:
    async with async_playwright() as p:
        b = await p.chromium.launch()
        page = await b.new_page(viewport={"width": 1500, "height": 1200},
                                device_scale_factor=2)
        await page.goto(SRC.as_uri(), wait_until="networkidle")
        h = await page.evaluate("document.body.scrollHeight")
        await page.set_viewport_size({"width": 1500, "height": int(h)})
        await page.screenshot(path=OUT, full_page=True)
        print(f"{OUT}  {OUT.stat().st_size / 1e6:.2f} MB  1500x{int(h)} @2x")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
