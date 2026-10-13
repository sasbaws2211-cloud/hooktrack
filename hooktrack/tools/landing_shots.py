"""Regenerate the landing page's product screenshots (static/shots/hooks.webp and posts.webp).

    python tools/landing_shots.py

It builds a throwaway database holding an invented sample workspace (no real accounts or posts), runs the real app
against it, signs in through the real login form and captures the Hooks page and the Posts board. Nothing here touches
your own database. Needs: Postgres (the dev one on 55434), Playwright with Edge or Chrome, and ffmpeg for WebP.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import psycopg2

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "static" / "shots"
ADMIN_URL = "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/postgres"
DB = "hooktrack_shots"
PORT = 8085
BASE = f"http://127.0.0.1:{PORT}"
EMAIL, PASSWORD = "you@yourbrand.example", "sample-workspace-pw-1"

# (handle, platform, niche, usual first-posts views, hooks: text, views, days ago)
ACCOUNTS = [
    ("@gymbrand", "TikTok", "fitness", [41000, 52000, 47000, 44000, 50000, 46000, 49000, 43000, 48000],
     [("the aesthetic of consistency", 709448, 5), ("your 3-step morning routine", 52310, 7), ("new drop, link in bio", 9870, 3)]),
    ("@dailydiy", "Instagram", "home", [18000, 22000, 19500, 21000, 17500, 23000, 20000, 18500, 21500],
     [("3 things I stopped buying for my flat", 61800, 6), ("wait for the transition", 14200, 4)]),
]


def sh(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    c = psycopg2.connect(ADMIN_URL)
    c.autocommit = True
    cur = c.cursor()
    cur.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    cur.execute(f'CREATE DATABASE "{DB}"')
    url = f"postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/{DB}"
    env = {**os.environ, "DATABASE_URL": url, "HOOKTRACK_SECRET": "shots-secret", "HOOKTRACK_SYNC_INTERVAL_HOURS": "0",
           "HOOKTRACK_BACKUP": "0"}
    for k in ("HOOKTRACK_PUBLIC_URL", "MAGICHOUR_API_KEY"):
        env.pop(k, None)
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{BASE}/api/public/legal", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("the app did not start")
        api = httpx.Client(base_url=BASE, headers={"origin": BASE}, timeout=60)
        r = api.post("/api/auth/signup", json={"email": EMAIL, "password": PASSWORD, "name": "Alex",
                                               "workspace_name": "Studio Sample", "timezone": "UTC"})
        r.raise_for_status()
        ws = r.json()["workspace"]["id"]
        # a trial would show the trial bar: this is a screenshot of the working pages
        db = psycopg2.connect(url)
        db.autocommit = True
        d = db.cursor()
        today = date.today()
        hook_ids = {}
        for handle, platform, niche, usual, hooks in ACCOUNTS:
            acc = api.post("/api/accounts", json={"handle": handle, "platform": platform, "niche": niche, "sync_enabled": False})
            acc.raise_for_status()
            aid = acc.json()["id"]
            for i, v in enumerate(usual):               # the account's normal: older, settled posts
                d.execute("""INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent,
                                                views_updated_at) VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now())""",
                          (ws, aid, today - timedelta(days=14 + i * 3), f"older post {i + 1}", v, f"{handle}-old{i}"))
            for text, views, ago in hooks:
                d.execute("""INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent,
                                                views_updated_at, comments, saves, shares)
                             VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now(), %s,%s,%s) RETURNING id""",
                          (ws, aid, today - timedelta(days=ago), text, views, f"{handle}-{ago}", views // 900, views // 120,
                           views // 400))
                hook_ids[text] = d.fetchone()[0]
        # the product's own flow turns decisions into work: a Scale hook makes a Scripted card, a Tweak hook an Idea card
        for text in ("the aesthetic of consistency", "your 3-step morning routine", "3 things I stopped buying for my flat"):
            api.post(f"/api/posts/{hook_ids[text]}/followup")
        first = api.get("/api/accounts").json()[0]["id"]
        for status, hook, offset in [("filmed", "Behind the scenes: how we plan a week of posts", 1),
                                     ("posted", "What I eat before a morning session", -4),
                                     ("idea", "Three mistakes new lifters make", 6)]:
            api.post("/api/posts", json={"account_id": first, "post_date": (today + timedelta(days=offset)).isoformat(),
                                         "status": status, "hook": hook, "views": 0, "comments": 0, "intent": False})
        db.close()

        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge")
            ctx = browser.new_context(viewport={"width": 1600, "height": 760}, device_scale_factor=1.5,
                                      color_scheme="dark", locale="en-US", timezone_id="UTC")
            page = ctx.new_page()
            if os.environ.get("SHOTS_DEBUG"):
                page.on("console", lambda m: print("console:", m.type, m.text[:200]))
                page.on("pageerror", lambda e: print("pageerror:", str(e)[:300]))
                page.on("response", lambda r: print("http", r.status, r.url.replace(BASE, "")[:90]))
                page.on("requestfailed", lambda r: print("FAILED", r.url.replace(BASE, "")[:90], r.failure))
            page.goto(f"{BASE}/#/login")
            page.fill("input[type=email]", EMAIL)
            page.fill("input[type=password]", PASSWORD)
            page.click("button:has-text('Sign in')")
            page.wait_for_selector("#nav", timeout=20000)
            # let the app finish rendering its own landing page first: a slow render that finishes after we navigate
            # would otherwise paint over the page we asked for
            page.wait_for_function("() => (document.querySelector('h1')||{}).innerText === 'Dashboard'", timeout=20000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            def tidy():
                for label in ("Got it", "Skip tips"):
                    for b in page.query_selector_all(f"button:has-text('{label}')"):
                        try:
                            b.click(timeout=500)
                        except Exception:
                            pass
                page.add_style_tag(content="#ai-badge,#setup-progress,#help-btn,#trialbar,#verifybar,.coach,.cookie-notice,"
                                           ".quick-tip,#cookie-notice{display:none!important}")

            # (file, route, the page's own heading, a row that proves its data is in, capture height)
            for name, route, heading, ready, height in (("hooks", "#/hooks", "Hooks", "table tbody tr", 730),
                                                        ("posts", "#/pipeline", "Posts", ".pipe-card", 500)):
                page.set_viewport_size({"width": 1600, "height": height})
                # the app can finish its own post-login redirect after we navigate, so insist that the page we want is
                # still the page on screen a moment later, and go there again if it is not
                for attempt in range(5):
                    page.evaluate("h => { location.hash = h; }", route)
                    try:
                        page.wait_for_function("h => (document.querySelector('h1')||{}).innerText === h", arg=heading, timeout=8000)
                        page.wait_for_selector(ready, timeout=8000)
                    except Exception:
                        continue
                    page.wait_for_timeout(1500)
                    if page.evaluate("() => (document.querySelector('h1')||{}).innerText") == heading:
                        break
                else:
                    seen = page.evaluate("() => ({h1: (document.querySelector('h1')||{}).innerText, hash: location.hash, "
                                         "text: (document.querySelector('#app')||{}).innerText})")
                    raise SystemExit(f"could not show the {heading} page: {str(seen)[:400]}")
                tidy()
                page.wait_for_timeout(300)
                png = OUT / f"{name}.png"
                page.screenshot(path=str(png))
                webp = OUT / f"{name}.webp"
                sh(["ffmpeg", "-y", "-v", "error", "-i", str(png), "-quality", "86", str(webp)])
                png.unlink()
                print(f"{name}: {webp.stat().st_size // 1024} KB")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
        cur.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
