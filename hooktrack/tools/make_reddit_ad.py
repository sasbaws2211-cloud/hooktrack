"""A silent landscape product ad (no voice-over), built the same way as the main demo (1280x720, real app size):

    1. add an account (a handle typed into the app)
    2. on the Hooks tab, send the winning video to Posts
    3. on Posts, attach the matching video
    4. schedule it

    python tools/make_reddit_ad.py                      # writes tools/demo_out/hooktrack-reddit-ad.mp4
    python tools/make_reddit_ad.py --out some/folder

Uses the same throwaway sample database, cursor, captions, spotlight rings and title/end cards as make_demo_video.py, so nothing
touches your own database and no real account or customer appears. The "publishing provider" here is the development mock, so
nothing is ever really posted.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_demo_video as demo          # noqa: E402

HANDLE = "@gymbrand"
WINNER = "the aesthetic of consistency"
OLDER = [41000, 52000, 47000, 44000, 50000, 46000, 49000, 43000, 48000, 45000]

AD_TEXT = """Headline: See which video won, then make the next one and schedule it, in one place.
Body: Add your account, send your winning video to Posts, attach the matching video and schedule it. Free on YouTube. hooktrack.site
"""

OPEN = demo.card_html('From "what worked?" to a <span>scheduled post</span>', "Add an account, send the winner to Posts, attach your next video, and schedule it.")
END = demo.card_html("See what worked. <span>Make the next one.</span>",
                     "Free on YouTube · hooktrack.site · Free hook scorer, no sign-up", "Start free · hooktrack.site")
# a feedback line, added below the usual card (before the closing of the outer .card div, not the first </div> it finds)
_feedback = "<p style='margin-top:22px;font-size:19px;color:#a5b4fc'>Built by one developer — I read every reply. Tell me what's missing.</p>"
END = _feedback.join(END.rsplit("</div>", 1)) + "</div>"


def seed_ad(url: str, ws: int) -> None:
    """The account's history, added behind the scenes right after the account is typed in: ten ordinary posts and one clear winner."""
    db = psycopg2.connect(url)
    db.autocommit = True
    d = db.cursor()
    d.execute("SELECT id FROM accounts WHERE workspace_id=%s AND handle=%s", (ws, HANDLE))
    aid = d.fetchone()[0]
    today = date.today()
    for i, v in enumerate(OLDER):
        d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at) "
                  "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now())", (ws, aid, today - timedelta(days=14 + i * 3), f"older post {i + 1}", v, f"ad-old{i}"))
    for text, views, ago in ((WINNER, 142000, 6), ("new drop, link in bio", 41000, 4), ("my gym bag essentials", 52000, 8)):
        d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at, comments, saves, shares) "
                  "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now(), %s,%s,%s)",
                  (ws, aid, today - timedelta(days=ago), text, views, f"ad-{ago}", views // 900, views // 120, views // 400))
    d.execute("UPDATE accounts SET niche='fitness' WHERE id=%s", (aid,))
    d.execute("INSERT INTO account_snapshots(account_id, workspace_id, day, followers) VALUES (%s,%s,current_date,%s)", (aid, ws, 48000))
    db.close()


def make_sample_video(path: Path) -> None:
    """A tiny placeholder clip for the "matching video": nothing real, just a coloured frame."""
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=0x4f46e5:s=360x640:d=3:r=24", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(path)], check=True)


# ---------------------------------------------------------------- the steps (same screens as make_demo_video.py, normal desktop size)
def step_add_account(d):
    d.nav("accounts")
    d.cap("1. Add your account")
    d.page.wait_for_selector("button:has-text('+ Account')", timeout=20000)
    d.page.wait_for_timeout(500)
    d.click("button:has-text('+ Account')")
    d.page.wait_for_selector("#h", timeout=8000)
    d.page.wait_for_timeout(400)
    d.type("#h", HANDLE, 90)
    d.page.wait_for_timeout(600)
    try:
        d.page.wait_for_function("!document.body.innerText.includes('Looking it up')", timeout=15000)
    except Exception:
        pass
    d.page.wait_for_timeout(400)
    d.click("#save")
    d.page.wait_for_selector("#h", state="detached", timeout=15000)
    d.page.wait_for_timeout(900)


def step_hooks(d):
    d.nav("hooks")
    d.page.wait_for_selector("table tbody tr", timeout=30000)
    d.cap("2. Spot the winning video and send it to Posts")
    d.page.wait_for_timeout(500)
    # the full Hooks table is wider than this recording's viewport; keep only the columns that matter for this
    # story, so the Post button sits on screen (the app itself is unchanged, only this recording's layout)
    trim = """() => {
      const t = [...document.querySelectorAll('table')].find(x => x.textContent.includes('Decision')); if (!t) return;
      const keep = ['hook', 'views', '× normal', 'decision', 'post'];
      const hide = [...t.querySelectorAll('thead th')].map((h, i) => keep.includes(h.textContent.trim().toLowerCase()) ? -1 : i).filter(i => i >= 0);
      t.querySelectorAll('tr').forEach(r => hide.forEach(i => { const c = r.children[i]; if (c) c.style.display = 'none'; }));
    }"""
    d.page.evaluate(trim)
    d.page.wait_for_timeout(500)
    d.spot("tr:has(td .pill:has-text('Scale')) td .pill", 0, 8)
    d.page.wait_for_timeout(1800)
    d.unspot()
    d.click("tr:has(td .pill:has-text('Scale')) button[data-post-hook]")
    d.page.wait_for_timeout(250)
    d.page.evaluate(trim)                          # the page redraws after the click: trim it again
    d.page.wait_for_timeout(1400)


def step_attach(d, video: Path):
    d.nav("pipeline")
    d.page.wait_for_selector(".pipe-card", timeout=20000)
    d.cap("3. Attach your matching video")
    d.page.wait_for_timeout(700)
    d.spot(f".pipe-card:has-text('{WINNER}')", 0, 6)
    d.page.wait_for_timeout(900)
    d.unspot()
    d.click(f".pipe-card:has-text('{WINNER}')")
    d.page.wait_for_selector("#vidfile", state="attached", timeout=10000)
    d.page.wait_for_timeout(900)
    d.to("#viddrop", steps=18)
    d.page.wait_for_timeout(500)
    d.page.set_input_files("#vidfile", str(video))
    d.page.wait_for_selector("#filmsched:visible", timeout=30000)
    d.page.wait_for_timeout(1200)


def step_schedule(d):
    d.cap("4. Schedule it")
    d.spot("#filmsched", 0, 8)
    d.page.wait_for_timeout(1000)
    d.unspot()
    d.click("#filmsched")
    d.page.wait_for_selector("#pubgo", timeout=10000)
    d.page.wait_for_timeout(1400)
    chips = d.page.locator("#slots .chip, #slots button")
    if chips.count():
        d.click("#slots .chip, #slots button")
        d.page.wait_for_timeout(600)
    d.click("#pubgo")
    try:
        d.page.wait_for_function("(document.querySelector('#toast') || {}).innerText && document.querySelector('#toast').innerText.includes('Scheduled')", timeout=6000)
    except Exception:
        d.page.evaluate("""() => {
          const t = document.getElementById('toast') || (() => { const e = document.createElement('div'); e.id = 'toast'; document.body.appendChild(e); return e; })();
          t.textContent = 'Scheduled: it will publish automatically'; t.style.cssText += 'opacity:1';
        }""")
        d.page.wait_for_timeout(1400)
        d.page.keyboard.press("Escape")               # close the schedule dialog
        d.page.wait_for_timeout(300)
        d.page.keyboard.press("Escape")               # close the post editor behind it, back to a clean board
    d.page.wait_for_timeout(1200)


def record(work: Path, out_dir: Path, url: str, video: Path) -> None:
    from playwright.sync_api import sync_playwright
    seeder = []
    ws = demo.workspace_of(url, demo.EMAIL)

    def add_and_seed(d):
        step_add_account(d)
        # this recording's sample server has no public address, so a real publish attempt in step 4 would correctly
        # refuse to schedule (it can't hand a provider a downloadable link); suppress just that error-styled notice
        # here so the recording shows the normal, successful screen a real public deployment would show (the fallback
        # success message a few lines below is what's then shown). Applied only now, since app.js (which defines the
        # real `toast`) has not loaded until the app's first real page is open.
        d.page.evaluate("""() => { const real = window.toast; window.toast = (msg, err) => { if (!err) real(msg, err); }; }""")
        t = threading.Thread(target=seed_ad, args=(url, ws), daemon=True)      # the history arrives while the next scene loads
        t.start()
        seeder.append(t)

    def hooks(d):
        for t in seeder:
            t.join(timeout=60)
        step_hooks(d)

    steps = [
        (add_and_seed, 2.0),
        (hooks, 2.0),
        (lambda d: step_attach(d, video), 2.0),
        (step_schedule, 2.0),
    ]
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": demo.W, "height": demo.H}, record_video_dir=str(work / "video"),
                                  record_video_size={"width": demo.W, "height": demo.H}, locale="en-US", timezone_id="UTC", color_scheme="light")
        ctx.add_init_script(demo.OVERLAY_JS)
        ctx.route("**/i.ytimg.com/**", lambda route: route.fulfill(body=demo.fake_thumb(route.request.url), content_type="image/png"))
        page = ctx.new_page()
        page.goto(f"{demo.BASE}/welcome")
        page.evaluate("([e, p]) => fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: e, password: p})})",
                      [demo.EMAIL, demo.PASSWORD])
        page.wait_for_timeout(500)
        d = demo.Driver(page, url)
        page.set_content(OPEN)
        page.wait_for_timeout(3200)
        for i, (fn, pad) in enumerate(steps):
            t0 = time.monotonic()
            try:
                fn(d)
            except Exception as e:
                demo.log(f"    ! step {i}: {type(e).__name__}: {str(e)[:140]}")
                if os.environ.get("DEMO_DEBUG"):
                    page.screenshot(path=str(out_dir / f"debug_step{i}.png"))
            d.cap("")
            demo.log(f"  step {i}: {time.monotonic() - t0:.1f}s")
            page.wait_for_timeout(int(pad * 1000))
        d.unspot()
        page.set_content(END)
        page.wait_for_timeout(3500)
        page.wait_for_timeout(500)
        ctx.close()
        browser.close()
    vids = sorted((work / "video").glob("*.webm"))
    shutil.copy(vids[-1], out_dir / "raw.webm")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(demo.ROOT / "tools" / "demo_out"))
    ap.add_argument("--name", default="hooktrack-reddit-ad")
    a = ap.parse_args()
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work_ad"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    video = work / "morning-routine-matching-video.mp4"
    make_sample_video(video)
    demo.log("1/3 the sample app")
    url = demo.build_database()
    env = {**os.environ, "DATABASE_URL": url, "HOOKTRACK_SECRET": "demo-secret", "HOOKTRACK_SYNC_INTERVAL_HOURS": "0", "HOOKTRACK_BACKUP": "0",
           "YOUTUBE_API_KEY": "AIzaDEMOKEYNOTREAL00000000000000000000", "SOCIAVAULT_API_KEY": "", "HOOKTRACK_ALLOW_MOCK_ADDONS": "1"}
    for k in ("RESEND_API_KEY", "HOOKTRACK_EMAIL_FROM", "HOOKTRACK_LATE_API_KEY", "HOOKTRACK_LATE_BASE_URL"):
        env[k] = ""         # present but empty, so the app never reads the real values from .env and no real email can be sent
    env["HOOKTRACK_PUBLIC_URL"] = demo.BASE      # the sample app's own address: the development publisher needs one for an uploaded file
    env["HOOKTRACK_ALLOW_LOCAL_EMAIL"] = "0"
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(demo.PORT)], cwd=demo.ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    uploaded = []
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{demo.BASE}/api/public/legal", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("the app did not start")
        api = httpx.Client(base_url=demo.BASE, headers={"origin": demo.BASE}, timeout=60)
        api.post("/api/auth/signup", json={"name": "Maya Johnson", "workspace_name": "Maya Studio", "email": demo.EMAIL, "password": demo.PASSWORD}).raise_for_status()
        ws = demo.workspace_of(url, demo.EMAIL)
        demo.give_plan(url, ws)
        r = api.put("/api/addons/publish", json={"enabled": True, "provider": "mock", "config": {"default_platforms": ["tiktok"]}})
        if r.status_code >= 300:
            demo.log(f"    ! could not switch on the development publisher: {r.status_code} {r.text[:120]}")
        demo.log("2/3 recording")
        record(work, out_dir, url, video)
        db = psycopg2.connect(url)
        cur = db.cursor()
        cur.execute("SELECT id FROM posts WHERE workspace_id=%s AND video_file <> ''", (ws,))
        uploaded = [(ws, row[0]) for row in cur.fetchall()]
        db.close()
        demo.log("3/3 encoding")
        final = out_dir / f"{a.name}.mp4"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(out_dir / "raw.webm"),
                        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
                        "-r", "30", "-an", "-movflags", "+faststart", str(final)], check=True)
        (out_dir / f"{a.name}-text.txt").write_text(AD_TEXT, encoding="utf-8")
        (out_dir / "raw.webm").unlink(missing_ok=True)
        demo.log(f"done: {final} ({demo.duration(final):.0f}s)")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
        for w, pid in uploaded:
            shutil.rmtree(demo.ROOT / "uploads" / str(w) / "videos" / str(pid), ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
        try:
            c = psycopg2.connect(demo.ADMIN_URL)
            c.autocommit = True
            c.cursor().execute(f'DROP DATABASE IF EXISTS "{demo.DB}" WITH (FORCE)')
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
