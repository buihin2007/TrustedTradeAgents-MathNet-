"""Record the demo as a video, on your machine, against your running stack (real data, real decisions).

    pip install playwright && python -m playwright install chromium
    docker compose up -d
    python scripts/demo/record_demo.py                       # asks for the demo user's password
    python scripts/demo/record_demo.py --rogue --grafana     # also runs the rogue agent and shows Grafana

Writes demo-<time>.webm (and .mp4 if ffmpeg is installed). Each scene gets a caption in the corner;
add the voice-over afterwards. Nothing is faked: if a step fails, the video shows it, so rehearse first.
"""

import argparse
import getpass
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout, sync_playwright

FINAL = {"placed", "held", "blocked", "rejected", "failed"}
CAPTION_JS = """
window.__caption = (title, line) => {
  let box = document.getElementById('__demo_caption');
  if (!box) {
    box = document.createElement('div');
    box.id = '__demo_caption';
    box.style.cssText = 'position:fixed;left:24px;bottom:24px;z-index:99999;max-width:560px;padding:14px 18px;' +
      'border-radius:14px;background:rgba(17,17,17,.88);color:#fff;font:15px/1.4 system-ui,sans-serif;' +
      'box-shadow:0 8px 30px rgba(0,0,0,.25)';
    document.body.appendChild(box);
  }
  box.innerHTML = '<div style="font-weight:600;font-size:16px;margin-bottom:2px"></div><div style="opacity:.85"></div>';
  box.children[0].textContent = title;
  box.children[1].textContent = line || '';
};
"""

SCENES = [
    {
        "title": "1 · The challenge's own example",
        "line": "1,000 l lactose-free milk by Monday. ~1,200 EUR is above the auto limit, so a person must sign off.",
        "text": "I need 1,000 litres of lactose-free milk by Monday. Find an option that meets our requirements "
                "based on price and availability.",
        "then_approve": True,
    },
    {
        "title": "2 · Within its authority",
        "line": "A small, complete order under the auto limit: the agent orders without asking.",
        "text": "20 kg chicken breast fillet for Friday",
    },
    {
        "title": "3 · Need → recipe → order",
        "line": "No dish named: the agent suggests dishes from what Valio Aimo has in stock that day.",
        "text": "What should I cook for 60 guests on Saturday? Nut-free please.",
    },
    {
        "title": "4 · Off topic",
        "line": "Not a purchasing request: Jev rejects it before anything reaches the gateway.",
        "text": "hello, how are you today?",
    },
]


def caption(page: Page, title: str, line: str = "", hold: float = 0) -> None:
    try:
        page.evaluate("([t, l]) => window.__caption && window.__caption(t, l)", [title, line])
    except Exception:
        pass
    if hold:
        page.wait_for_timeout(int(hold * 1000))


def status(page: Page) -> str | None:
    badge = page.locator("[data-status]").first
    return badge.get_attribute("data-status") if badge.count() else None


def answer(page: Page, question) -> str:
    """Answer like a buyer would: first sensible option; for dishes pick two and confirm."""
    field = question.get_attribute("data-field") or ""
    options = question.locator("[data-testid=option]")
    values = [options.nth(i).get_attribute("data-value") or "" for i in range(options.count())]
    usable = [i for i, v in enumerate(values) if v not in ("cancel", "more") and not v.startswith("date:")]
    if field == "dishes":
        for i in usable[:2]:
            options.nth(i).click()
            page.wait_for_timeout(500)
        question.locator("[data-testid=send]").click()
        return "picked two dishes"
    if usable:
        label = options.nth(usable[0]).inner_text().split("\n")[0]
        options.nth(usable[0]).click()
        return label
    question.locator("input").fill("40")
    question.locator("[data-testid=send]").click()
    return "typed 40"


def run_request(page: Page, base: str, scene: dict, timeout: float) -> str:
    page.goto(f"{base}/requests")
    caption(page, scene["title"], scene["line"], hold=2.5)
    box = page.locator("textarea")
    box.click()
    box.type(scene["text"], delay=28)
    page.wait_for_timeout(600)
    page.get_by_role("button", name=re.compile("Send to agent")).click()
    page.wait_for_url(re.compile(r"/requests/[0-9a-f-]{36}"), timeout=20000)
    caption(page, scene["title"], scene["line"])
    deadline, answered = time.monotonic() + timeout, 0
    while time.monotonic() < deadline:
        current = status(page)
        if current in FINAL:
            break
        question = page.locator("[data-testid=question]").last
        if current == "asking" and question.count() and answered < 6:
            page.wait_for_timeout(1800)  # let the viewer read the question
            picked = answer(page, question)
            answered += 1
            caption(page, scene["title"], f"Buyer answers: {picked}")
            page.wait_for_timeout(1500)
            continue
        page.wait_for_timeout(500)
    final = status(page) or "unknown"
    page.mouse.wheel(0, 4000)
    caption(page, scene["title"], f"Gateway outcome: {final}", hold=4)
    return final


def approve(page: Page, base: str) -> None:
    page.goto(f"{base}/approvals")
    caption(page, "Human sign-off", "The held order waits here. Only the mandate signer's login can approve it.", hold=3)
    button = page.get_by_role("button", name=re.compile("Approve this option")).first
    try:
        button.wait_for(timeout=10000)
        button.click()
        page.wait_for_timeout(3000)
        caption(page, "Human sign-off", "Approved: the gateway re-prices and places the order.", hold=3)
    except PlaywrightTimeout:
        caption(page, "Human sign-off", "No held order to approve.", hold=2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--email", default="manager@demo-hotel.example")
    ap.add_argument("--grafana", action="store_true", help="end on the Valio Aimo dashboard (http://localhost:3001)")
    ap.add_argument("--grafana-url", default="http://localhost:3001")
    ap.add_argument("--rogue", action="store_true", help="run scripts/rogue_agent.py before the dashboard")
    ap.add_argument("--scenes", default="1,2,3,4", help="which scenes to record, e.g. 1,3")
    ap.add_argument("--timeout", type=float, default=150, help="seconds per request")
    ap.add_argument("--show", action="store_true", help="show the browser window while recording")
    args = ap.parse_args()
    password = os.environ.get("DEMO_PASSWORD") or getpass.getpass(f"Password for {args.email}: ")
    grafana_password = None
    if args.grafana:
        grafana_password = os.environ.get("GRAFANA_ADMIN_PASSWORD") or getpass.getpass("Grafana admin password: ")
    chosen = [SCENES[int(n) - 1] for n in args.scenes.split(",") if n.strip()]
    out_dir = Path("demo-video")
    out_dir.mkdir(exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.show, slow_mo=60)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            record_video_dir=str(out_dir),
            record_video_size={"width": 1440, "height": 900},
        )
        context.add_init_script(CAPTION_JS)
        page = context.new_page()

        page.goto(f"{args.base}/login")
        caption(page, "Trusted Trade Agents", "A purchasing agent that buys through Valio Aimo's own MCP gateway", hold=3)
        page.fill("input[type=email]", args.email)
        page.fill("input[type=password]", password)
        page.locator("button").first.click()
        page.wait_for_url(re.compile(r"/requests"), timeout=20000)

        results = []
        for scene in chosen:
            final = run_request(page, args.base, scene, args.timeout)
            results.append((scene["title"], final))
            print(f"{scene['title']}: {final}")
            if scene.get("then_approve") and final == "held":
                approve(page, args.base)

        page.goto(f"{args.base}/audit")
        caption(page, "Audit trail", "Every decision, who made it and why, hash-chained and linked to its trace.", hold=5)
        page.mouse.wheel(0, 1500)
        page.wait_for_timeout(2000)

        if args.rogue:
            caption(page, "A rogue agent", "Scraping, unrequested items, user-only tools, bursts…", hold=1)
            env = os.environ | {k: v for k, v in dotenv_values(".env").items() if v}  # AGENT_KEY
            subprocess.run([sys.executable, "scripts/rogue_agent.py"], check=False, env=env)

        if args.grafana:
            page.goto(f"{args.grafana_url}/login")
            page.fill("input[name=user]", "admin")
            page.fill("input[name=password]", grafana_password)
            page.keyboard.press("Enter")
            page.wait_for_timeout(5000)
            caption(page, "Valio Aimo's view", "Every agent call the gateway allowed or blocked, live.", hold=8)

        video = page.video
        context.close()
        browser.close()
        source = Path(video.path())

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    target = out_dir / f"demo-{stamp}.webm"
    source.rename(target)
    print(f"\nVideo: {target}")
    if shutil.which("ffmpeg"):
        mp4 = target.with_suffix(".mp4")
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(target), "-c:v", "libx264",
                        "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", str(mp4)], check=False)
        if mp4.exists():
            print(f"MP4:   {mp4}")
    for title, final in results:
        print(f"  {title}: {final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
