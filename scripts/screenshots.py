"""Capture README screenshots and the GitHub social preview from a running app.

Usage (app running, e.g. `uv run d2d serve`; uses the installed Google Chrome):
    uv run --with playwright python scripts/screenshots.py --url http://127.0.0.1:8081

Writes docs/images/*.png. The Copilot shot asks a real question, so the LLM must be reachable.
"""

from __future__ import annotations

import argparse
import base64
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "images"
VIEWPORT = {"width": 1440, "height": 900}
PAGES = [
    ("overview.png", "/", ".device-tile"),
    ("story.png", "/stories?category=load_shift", ".story-card"),
    ("changelog.png", "/changelog", "tbody tr"),
    ("events.png", "/events", "tbody tr"),
    ("devices.png", "/devices", "tbody tr"),
    ("review.png", "/review", "h1"),
]
QUESTION = "What should I do about the washer?"

SOCIAL = """<!doctype html><html><head><style>
* {{ box-sizing: border-box; margin: 0; }}
body {{ width: 1280px; height: 640px; overflow: hidden; position: relative; color: #e8ecf5;
  font-family: -apple-system, 'Segoe UI', Inter, sans-serif;
  background: linear-gradient(135deg, #141b2d 0%, #1f2a4a 60%, #3157d5 140%); }}
.left {{ position: absolute; left: 64px; top: 72px; width: 540px; }}
.logo {{ display: inline-block; background: #3157d5; color: #fff; font-weight: 800;
  border-radius: 10px; padding: 8px 12px; font-size: 22px; }}
h1 {{ font-size: 56px; line-height: 1.05; margin-top: 28px; font-weight: 800; color: #fff; }}
.beta {{ font-size: 18px; font-weight: 800; letter-spacing: .08em; background: #f6c453;
  color: #141b2d; border-radius: 6px; padding: 3px 9px; vertical-align: 16px; margin-left: 8px; }}
p.tag {{ font-size: 24px; color: #b9c3da; margin-top: 18px; line-height: 1.35; }}
ul {{ list-style: none; padding: 0; margin-top: 26px; }}
li {{ font-size: 19px; color: #d7deec; margin: 9px 0; }}
li b {{ color: #f6c453; }}
.foot {{ position: absolute; left: 64px; bottom: 38px; font-size: 16px; color: #8c97b0; }}
.shot {{ position: absolute; left: 650px; top: 78px; width: 800px; border-radius: 14px;
  box-shadow: 0 30px 80px rgba(0,0,0,.55); border: 1px solid rgba(255,255,255,.12);
  transform: perspective(1600px) rotateY(-9deg) rotateX(3deg); transform-origin: left center; }}
</style></head><body>
<div class="left">
  <span class="logo">D2D</span>
  <h1>Change Control<span class="beta">BETA</span></h1>
  <p class="tag">Data to Decision with AI: raw device changes &rarr; labeled stories &rarr;
  plain-language answers.</p>
  <ul>
    <li><b>&rarr;</b> Changelog, change events and linked stories</li>
    <li><b>&rarr;</b> Precision-first classifier with human review</li>
    <li><b>&rarr;</b> Local Copilot (Ollama) with grounded citations</li>
    <li><b>&rarr;</b> Feedback that calibrates every layer</li>
  </ul>
</div>
<img class="shot" src="data:image/png;base64,{shot}">
<div class="foot">Demo on a simulated smart home &middot; no real devices or company systems</div>
</body></html>"""


def settle(page: Page, selector: str) -> None:
    page.locator(selector).first.wait_for(timeout=20_000)
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(300)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8081")
    parser.add_argument("--skip-copilot", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        page.goto(args.url + "/")
        page.evaluate("localStorage.setItem('d2d.role', 'owner')")
        for name, path, selector in PAGES:
            page.goto(args.url + path)
            settle(page, selector)
            if name == "story.png":  # open the first load-shift story
                page.locator(".story-card").first.click()
                settle(page, ".card h2")
            page.screenshot(path=OUT / name)
            print("wrote", name)
        page.goto(args.url + "/")
        settle(page, ".device-tile")
        overview = base64.b64encode(page.screenshot()).decode()
        if not args.skip_copilot:
            page.goto(args.url + "/ask")
            settle(page, "h1")
            page.get_by_role("button", name=QUESTION).click()
            page.locator(".answer-meta").first.wait_for(timeout=180_000)
            page.wait_for_timeout(500)
            page.screenshot(path=OUT / "copilot.png")
            print("wrote copilot.png")
        card = browser.new_page(viewport={"width": 1280, "height": 640})
        card.set_content(SOCIAL.format(shot=overview))
        card.wait_for_timeout(300)
        card.screenshot(path=OUT / "social-preview.png")
        print("wrote social-preview.png")
        browser.close()


if __name__ == "__main__":
    main()
