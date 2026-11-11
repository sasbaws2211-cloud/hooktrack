"""Records HookTrack's product demo: a narrated walkthrough of the real app.

    python tools/make_demo_video.py                       # writes tools/demo_out/hooktrack-demo.mp4 (+ .srt and a script)
    python tools/make_demo_video.py --voice en-US-AndrewNeural --out some/folder

What it does, start to finish:
  1. builds a throwaway database holding an invented sample workspace (no real accounts, posts or customers) and runs the real
     app against it on its own port. Nothing here touches your own database;
  2. writes the voice-over for each scene with a neural text-to-speech voice (edge-tts, a free Microsoft service that needs
     internet; the script is public marketing copy) and measures how long each line takes;
  3. drives a real browser through the app, giving each scene exactly as long as its narration, with a visible cursor, and
     records the screen;
  4. joins the picture and the narration with ffmpeg into an mp4 you can upload to YouTube.
Needs: Postgres (the dev one on 55434), Playwright with Chrome, ffmpeg on the PATH, and `pip install edge-tts`.
After uploading, give the new YouTube link to whoever maintains the site: the demo video id appears in static/demo-player.js,
static/tool-cta.js, static/app.js and a few pages.
"""
from __future__ import annotations

import argparse
import asyncio
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
ADMIN_URL = "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/postgres"
DB = "hooktrack_demo"
PORT = 8086
BASE = f"http://127.0.0.1:{PORT}"
EMAIL, PASSWORD = "demo@yourbrand.example", "sample-workspace-pw-1"
W, H = 1280, 720

# (what is said, in plain words; the scene's actions are below). Keep each line short enough to read as one breath.
NARRATION = [
    ("intro", "Most creators check their views and guess what worked. HookTrack compares every video with that account's own normal, "
              "and tells you what to do next: scale it, tweak it, or stop."),
    ("dashboard", "Here is a sample workspace. The dashboard shows this week's views, the hooks worth scaling, and the posts that need a rethink."),
    ("posts", "On the Posts board, every video carries a chip. A star means it beat that account's usual views by a wide margin."),
    ("hooks", "The Hooks page turns that into a plain call for every hook. Scale the winners, tweak the near misses, and stop what is not working."),
    ("accounts", "A brand new account has no history yet, so HookTrack compares its first videos with similar accounts, and counts down to its own baseline."),
    ("discover", "Discover finds recent videos that did several times better than their own creator's usual, from creators your size. "
                 "Make your own version with AI, or save the idea for later."),
    ("tool", "There are free tools too. The hook scorer rates your opening line out of one hundred, and shows exactly what to fix."),
    ("pricing", "Start free with YouTube. Solo is twelve dollars a month, and Pro is thirty nine, for bigger portfolios and small teams. "
                "AI help is included on every plan. Or take the lifetime deal: one payment, while the early supporter spots last."),
    ("outro", "HookTrack. See what really worked, and make more of it."),
]

ACCOUNTS = [   # handle, platform, niche, usual views of the older posts, recent posts (text, views, days ago)
    ("@gymbrand", "TikTok", "fitness", [41000, 52000, 47000, 44000, 50000, 46000, 49000, 43000, 48000],
     [("the aesthetic of consistency", 709448, 5), ("your 3-step morning routine", 52310, 7), ("new drop, link in bio", 9870, 3)]),
    ("@dailydiy", "Instagram", "home", [18000, 22000, 19500, 21000, 17500, 23000, 20000, 18500, 21500],
     [("3 things I stopped buying for my flat", 61800, 6), ("wait for the transition", 14200, 4)]),
    ("@studio.shorts", "YouTube", "education", [3200, 4100, 3800, 4400, 3600, 4000, 3900, 4300, 3700],
     [("how I study in 20 minutes a day", 15800, 6), ("stop rereading your notes", 4100, 3)]),
]
NEWBIE = ("@newbie.fit", "TikTok", "fitness", [("why I stopped doing crunches", 1100, 6), ("my first week of home workouts", 420, 9),
                                               ("3 mistakes beginners make at the gym", 310, 12)])
DISCOVER = [   # video id, niche, band, title, views, usual, subscribers, days ago
    ("demoVideo001", "fitness", "under-1k", "Why my 5am workouts finally stuck", 12400, 310, 640, 3),
    ("demoVideo002", "fitness", "1k-10k", "3 mistakes beginners make at the gym", 88000, 9000, 6200, 4),
    ("demoVideo003", "food", "under-1k", "The 10-minute dinner nobody talks about", 15300, 2100, 820, 3),
    ("demoVideo004", "education", "1k-10k", "Stop rereading your notes, do this instead", 41000, 4800, 5100, 5),
    ("demoVideo005", "fitness", "10k-100k", "I trained like this for 30 days", 156000, 24000, 41000, 2),
    ("demoVideo006", "lifestyle", "under-1k", "What a quiet Sunday reset really looks like", 9800, 1200, 910, 6),
]


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- the sample workspace
def build_database() -> str:
    c = psycopg2.connect(ADMIN_URL)
    c.autocommit = True
    cur = c.cursor()
    cur.execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
    cur.execute(f'CREATE DATABASE "{DB}"')
    return f"postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/{DB}"


def seed(url: str, api: httpx.Client, ws: int) -> None:
    db = psycopg2.connect(url)
    db.autocommit = True
    d = db.cursor()
    today = date.today()
    d.execute("INSERT INTO platform_settings(key, value) VALUES ('billing_enabled', 'true') ON CONFLICT (key) DO UPDATE SET value='true'")
    d.execute("INSERT INTO workspace_subscriptions(workspace_id, plan_code, status) VALUES (%s,'pro','comp') "
              "ON CONFLICT (workspace_id) DO UPDATE SET plan_code='pro', status='comp'", (ws,))
    hook_ids = {}
    for handle, platform, niche, usual, hooks in ACCOUNTS:
        aid = api.post("/api/accounts", json={"handle": handle, "platform": platform, "niche": niche, "sync_enabled": False}).json()["id"]
        d.execute("INSERT INTO account_snapshots(account_id, workspace_id, day, followers) VALUES (%s,%s,current_date,%s)", (aid, ws, 48000))
        for i, v in enumerate(usual):
            d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at) "
                      "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now())", (ws, aid, today - timedelta(days=14 + i * 3), f"older post {i + 1}", v, f"{handle}-old{i}"))
        for text, views, ago in hooks:
            d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at, comments, saves, shares) "
                      "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now(), %s,%s,%s) RETURNING id",
                      (ws, aid, today - timedelta(days=ago), text, views, f"{handle}-{ago}", views // 900, views // 120, views // 400))
            hook_ids[text] = d.fetchone()[0]
    # a believable run of daily views per account. Last week is steady, this week (the days so far) is clearly up, and the older weeks
    # are lower: so the dashboard shows growth, one account earns SCALE, and the weekly chart is not flat.
    monday = today - timedelta(days=today.weekday())
    so_far = today.weekday() + 1
    daily = {"@gymbrand": (6500, 11000), "@dailydiy": (2100, 3600), "@studio.shorts": (420, 800), "@newbie.fit": (40, 70)}
    d.execute("SELECT id, handle FROM accounts WHERE workspace_id=%s", (ws,))
    for aid, h in d.fetchall():
        base, last_week = daily.get(h, (100, 200))
        for back in range(0, 63):
            day = today - timedelta(days=back)
            wobble = 0.88 + ((back * 37) % 24) / 100.0
            if day >= monday:
                v = last_week * 7 * 1.5 / so_far * wobble          # this week so far: 50% above the whole of last week
            elif day >= monday - timedelta(days=7):
                v = last_week * wobble
            else:
                v = base * wobble
            d.execute("INSERT INTO daily_views(account_id, workspace_id, day, views, source) VALUES (%s,%s,%s,%s,'manual') ON CONFLICT DO NOTHING",
                      (aid, ws, day, int(v)))
    # a top-scoring hook posted this week (and old enough to be judged) is what lets the dashboard call an account SCALE
    win_day = monday if (today - monday).days >= 2 else today - timedelta(days=5)
    d.execute("UPDATE posts SET post_date=%s WHERE workspace_id=%s AND hook='the aesthetic of consistency'", (win_day, ws))
    for text, v, sc, c in (("the aesthetic of consistency", 9, 9, 9), ("your 3-step morning routine", 8, 8, 8), ("3 things I stopped buying for my flat", 9, 8, 9),
                           ("how I study in 20 minutes a day", 9, 8, 8), ("new drop, link in bio", 4, 4, 5), ("wait for the transition", 6, 6, 5)):
        d.execute("UPDATE posts SET v=%s, s=%s, c=%s, score_by='manual' WHERE workspace_id=%s AND hook=%s", (v, sc, c, ws, text))
    first = api.get("/api/accounts").json()[0]["id"]
    for status, hook, offset in (("idea", "Three mistakes new lifters make", 6), ("idea", "What a realistic week of meal prep looks like", 7),
                                 ("scripted", "Behind the scenes: how we plan a week of posts", 2), ("filmed", "What I eat before a morning session", 1),
                                 ("scripted", "The 10-minute warm-up I never skip", 3)):
        api.post("/api/posts", json={"account_id": first, "post_date": (today + timedelta(days=offset)).isoformat(), "status": status, "hook": hook,
                                     "views": 0, "comments": 0, "intent": False})
    handle, platform, niche, posts = NEWBIE
    nid = api.post("/api/accounts", json={"handle": handle, "platform": platform, "niche": niche, "sync_enabled": False}).json()["id"]
    d.execute("INSERT INTO account_snapshots(account_id, workspace_id, day, followers) VALUES (%s,%s,current_date,%s)", (nid, ws, 420))
    for text, views, ago in posts:
        d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at) "
                  "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now())", (ws, nid, today - timedelta(days=ago), text, views, f"{handle}-{ago}"))
    for text in ("the aesthetic of consistency", "your 3-step morning routine", "3 things I stopped buying for my flat"):
        api.post(f"/api/posts/{hook_ids[text]}/followup")
    # what a typical small TikTok account gets, so the new account is compared with similar accounts until it has its own normal
    for band, p25, med, p75 in (("under-1k", 231, 554, 764), ("1k-10k", 596, 1552, 11906), ("10k-100k", 7814, 19710, 34186), ("100k-plus", 65794, 159516, 257709)):
        d.execute("INSERT INTO peer_benchmarks(platform, fmt, niche, band, source, n_accounts, n_posts, p25, median, p75) "
                  "VALUES ('tiktok','main','general',%s,'seed',20,300,%s,%s,%s) ON CONFLICT DO NOTHING", (band, p25, med, p75))
    for vid, niche, band, title, views, usual, subs, ago in DISCOVER:
        d.execute("INSERT INTO discover_items(platform, video_id, niche, band, channel_id, channel_title, title, duration_s, published_at, views, "
                  "channel_median, multiple, subscribers) VALUES ('youtube',%s,%s,%s,'UC0000000000000000000001','Sample creator',%s,30,now() - make_interval(days => %s),%s,%s,%s,%s)",
                  (vid, niche, band, title, ago, views, usual, round(views / usual, 2), subs))
    db.close()


# ---------------------------------------------------------------- the voice
async def _speak(text: str, voice: str, path: Path, rate: str) -> None:
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(str(path))


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip())


def make_voice(work: Path, voice: str, rate: str) -> list[tuple[str, str, Path, float]]:
    out = []
    for key, text in NARRATION:
        p = work / f"{key}.mp3"
        asyncio.run(_speak(text, voice, p, rate))
        out.append((key, text, p, duration(p)))
        log(f"  voice {key}: {out[-1][3]:.1f}s")
    return out


# ---------------------------------------------------------------- the recording
CURSOR_JS = """
(() => {
  const add = () => {
    if (document.getElementById('demo-cursor')) return;
    const c = document.createElement('div'); c.id = 'demo-cursor';
    c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:rgba(79,70,229,.28);border:2px solid #4f46e5;box-shadow:0 2px 8px rgba(0,0,0,.25);z-index:2147483647;pointer-events:none;transition:transform .12s;';
    document.documentElement.appendChild(c);
    document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', () => { c.style.transform = 'scale(.7)'; }, true);
    document.addEventListener('mouseup', () => { c.style.transform = 'scale(1)'; }, true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""
def fake_thumb(seed: str) -> bytes:
    """A pleasant 320x180 placeholder picture for a sample video, made here: no real creator's thumbnail is ever shown."""
    import hashlib, io
    from PIL import Image, ImageDraw
    h = hashlib.sha256(seed.encode()).digest()
    top, bot = (60 + h[0] % 120, 70 + h[1] % 120, 160 + h[2] % 90), (30 + h[3] % 90, 40 + h[4] % 90, 90 + h[5] % 120)
    img = Image.new("RGB", (320, 180))
    px = img.load()
    for y in range(180):
        t = y / 179
        row = tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3))
        for x in range(320):
            px[x, y] = row
    dr = ImageDraw.Draw(img)
    dr.rounded_rectangle((118, 14, 202, 166), radius=14, outline=(255, 255, 255), width=3)
    dr.polygon([(148, 70), (148, 110), (180, 90)], fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


HIDE_CSS = ("#ai-badge,#setup-progress,#trialbar,#verifybar,#coach,.coach,.cookie-notice,#cookie-notice,#guide-fab,#guide-panel"
            "{display:none!important}")
# injected into every page before it draws, so tips and banners never flash on screen
HIDE_JS = ("(() => { const css = %r; const add = () => { if (document.getElementById('demo-hide')) return; const s = document.createElement('style');"
           " s.id = 'demo-hide'; s.textContent = css; (document.head || document.documentElement).appendChild(s); };"
           " add(); document.addEventListener('DOMContentLoaded', add); })();" % HIDE_CSS)


class Driver:
    def __init__(self, page):
        self.page = page
        self.x, self.y = 640, 360

    def glide(self, x: float, y: float, steps: int = 28) -> None:
        self.page.mouse.move(x, y, steps=steps)
        self.x, self.y = x, y

    def to(self, selector: str, steps: int = 28, nth: int = 0, dx: float = 0, dy: float = 0) -> bool:
        t_start = time.monotonic()
        try:
            return self._to(selector, steps, nth, dx, dy)
        finally:
            if os.environ.get("DEMO_DEBUG"):
                log(f"      to({selector!r}, nth={nth}) {time.monotonic() - t_start:.1f}s")

    def _to(self, selector: str, steps: int, nth: int, dx: float, dy: float) -> bool:
        loc = self.page.locator(selector).nth(nth)
        try:
            # not scroll_into_view_if_needed: it waits for the element to stop moving, and cards with lazy pictures keep shifting
            loc.evaluate("e => { const r = e.getBoundingClientRect(); if (r.top < 90 || r.bottom > innerHeight - 20) e.scrollIntoView({block: 'center'}); }", timeout=3000)
            self.page.wait_for_timeout(250)
            box = loc.bounding_box()
        except Exception:
            return False
        if not box:
            return False
        self.glide(box["x"] + box["width"] / 2 + dx, box["y"] + box["height"] / 2 + dy, steps)
        return True

    def click(self, selector: str, nth: int = 0) -> bool:
        if not self.to(selector, nth=nth):
            return False
        self.page.wait_for_timeout(250)
        self.page.mouse.down(); self.page.wait_for_timeout(80); self.page.mouse.up()
        return True

    def scroll(self, y: int, ms: int = 2500) -> None:
        self.page.evaluate("""([y, ms]) => new Promise(res => { const s = window.scrollY, t0 = performance.now();
            const ease = t => t < .5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
            const step = now => { const t = Math.min(1, (now - t0) / ms); window.scrollTo(0, s + (y - s) * ease(t)); t < 1 ? requestAnimationFrame(step) : res(); };
            requestAnimationFrame(step); })""", [y, ms])

    def tidy(self) -> None:
        self.page.add_style_tag(content=HIDE_CSS)
        for label in ("Got it", "Skip tips", "Accept", "Dismiss"):
            for b in self.page.query_selector_all(f"button:has-text('{label}')"):
                try:
                    b.click(timeout=400)
                except Exception:
                    pass

    def goto_app(self, tab: str) -> None:
        """Click the real navigation link, so the viewer sees how it works."""
        if not self.click(f"#nav a[data-tab='{tab}']"):
            self.page.goto(f"{BASE}/app#/{tab}")
        self.page.wait_for_timeout(900)
        self.tidy()


def record(scenes, work: Path, out_dir: Path) -> list[tuple[str, float, float]]:
    from playwright.sync_api import sync_playwright
    timeline = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(work / "video"), record_video_size={"width": W, "height": H},
                                  locale="en-US", timezone_id="UTC", color_scheme="light")
        ctx.add_init_script(CURSOR_JS)
        ctx.add_init_script(HIDE_JS)
        ctx.route("**/i.ytimg.com/**", lambda route: route.fulfill(body=fake_thumb(route.request.url), content_type="image/png"))
        r = ctx.request.post(f"{BASE}/api/auth/login", data={"email": EMAIL, "password": PASSWORD})
        if r.status != 200:
            raise SystemExit(f"could not sign in the sample user ({r.status})")
        page = ctx.new_page()
        t_video = time.monotonic()
        d = Driver(page)
        page.goto(f"{BASE}/welcome")
        page.wait_for_timeout(1500)
        d.tidy()
        page.reload(); page.wait_for_timeout(1200); d.tidy()
        for key, text, audio, dur in scenes:
            t0 = time.monotonic()
            end = t0 + dur + 0.9
            timeline.append((key, t0 - t_video, dur))
            ACTIONS[key](d, page, lambda: max(0.0, end - time.monotonic()))
            left = end - time.monotonic()
            if left > 0:
                page.wait_for_timeout(int(left * 1000))
            log(f"  scene {key}: {time.monotonic() - t0:.1f}s")
        page.wait_for_timeout(600)
        ctx.close()
        browser.close()
    vids = sorted((work / "video").glob("*.webm"))
    shutil.copy(vids[-1], out_dir / "raw.webm")
    return timeline


# ---- the scenes: what is on screen while each line is spoken
def s_intro(d, page, left):
    d.glide(640, 300, 20); page.wait_for_timeout(1200)
    d.to(".hero a.btn, .hero .btn", nth=0); page.wait_for_timeout(500)
    d.scroll(320, int(min(3500, left() * 700))); page.wait_for_timeout(900)


def s_dashboard(d, page, left):
    d.goto_app("dashboard")
    page.wait_for_selector("h1", timeout=15000)
    d.glide(300, 330); page.wait_for_timeout(900)
    d.glide(700, 330, 40); page.wait_for_timeout(500)
    d.scroll(520, int(min(4500, left() * 700)))
    page.wait_for_timeout(300)
    d.scroll(900, int(min(2500, max(800, left() * 500))))


def s_posts(d, page, left):
    d.goto_app("pipeline")
    page.wait_for_timeout(1200)
    if d.to(".pipe-card", nth=0):
        page.wait_for_timeout(1500)
    if d.to(".pill", nth=2):
        page.wait_for_timeout(1500)
    d.glide(900, 350, 30)


def s_hooks(d, page, left):
    d.goto_app("hooks")
    page.wait_for_timeout(1200)
    d.to("table tbody tr", nth=0); page.wait_for_timeout(1500)
    d.to("table tbody tr", nth=2, dx=200); page.wait_for_timeout(1500)
    d.scroll(260, int(min(3500, left() * 500)))


def s_accounts(d, page, left):
    d.goto_app("accounts")
    page.wait_for_timeout(1000)
    d.to("text=posts to its own baseline", nth=0); page.wait_for_timeout(1800)
    d.goto_app("hooks")
    page.wait_for_timeout(700)
    d.scroll(600, int(min(2500, left() * 500)))
    d.to("text=vs peers", nth=0); page.wait_for_timeout(1200)


def s_discover(d, page, left):
    d.goto_app("discover")
    page.wait_for_timeout(900)
    d.to("#db"); page.select_option("#db", ""); page.wait_for_timeout(700)        # it opens on the viewer's own niche and size: show the filters
    d.to("#dn"); page.select_option("#dn", "fitness"); page.wait_for_timeout(1000)
    d.to(".disc-card", nth=1); page.wait_for_timeout(1200)
    d.to("[data-mk]", nth=1); page.wait_for_timeout(1400)
    d.click("[data-sv]", nth=1); page.wait_for_timeout(1000)
    d.to(".disc-card", nth=2, steps=18)


def s_tool(d, page, left):
    page.goto(f"{BASE}/tools/video-hook-scorer"); page.wait_for_timeout(1200); d.tidy()
    d.glide(500, 380, 25); page.wait_for_timeout(800)
    d.click("#hk-sample"); page.wait_for_timeout(1500)
    d.scroll(650, int(min(5000, left() * 700)))


def s_pricing(d, page, left):
    page.goto(f"{BASE}/welcome"); page.wait_for_timeout(1200); d.tidy()
    page.evaluate("document.querySelector('#pricing').scrollIntoView({behavior:'smooth', block:'start'})"); page.wait_for_timeout(1800)
    for i in range(4):
        d.to("#plans .plan", nth=i, steps=24); page.wait_for_timeout(1300)
    d.to("#plans .plan.life a.btn", nth=0)


def s_outro(d, page, left):
    page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})"); page.wait_for_timeout(1200)
    d.glide(640, 330, 30)


ACTIONS = {"intro": s_intro, "dashboard": s_dashboard, "posts": s_posts, "hooks": s_hooks, "accounts": s_accounts,
           "discover": s_discover, "tool": s_tool, "pricing": s_pricing, "outro": s_outro}


# ---------------------------------------------------------------- joining picture and voice
def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def assemble(scenes, timeline, out_dir: Path, name: str) -> Path:
    start = max(0.0, timeline[0][1] - 0.5)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(out_dir / "raw.webm")]
    for _, _, audio, _ in scenes:
        cmd += ["-i", str(audio)]
    parts, labels = [], []
    for i, (key, t0, dur) in enumerate(timeline):
        ms = int(max(0, t0 - start) * 1000) + 150
        parts.append(f"[{i + 1}:a]adelay={ms}|{ms},volume=1.0[a{i}]")
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[aout]")
    final = out_dir / f"{name}.mp4"
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]", "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(final)]
    subprocess.run(cmd, check=True)
    with open(out_dir / f"{name}.srt", "w", encoding="utf-8") as f:
        for i, ((key, t0, dur), (_, text, _, _)) in enumerate(zip(timeline, scenes), 1):
            a = t0 - start + 0.15
            f.write(f"{i}\n{srt_time(a)} --> {srt_time(a + dur)}\n{text}\n\n")
    (out_dir / f"{name}-script.txt").write_text("\n\n".join(f"[{k}] {t}" for k, t in NARRATION) + "\n", encoding="utf-8")
    return final


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "tools" / "demo_out"))
    ap.add_argument("--name", default="hooktrack-demo")
    ap.add_argument("--voice", default="en-US-AriaNeural", help="any edge-tts voice, e.g. en-US-AndrewNeural or en-GB-SoniaNeural")
    ap.add_argument("--rate", default="-4%", help="speaking speed, e.g. -10%% slower, +0%% normal")
    a = ap.parse_args()
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} is not on the PATH")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)

    log("1/4 the sample workspace")
    url = build_database()
    env = {**os.environ, "DATABASE_URL": url, "HOOKTRACK_SECRET": "demo-secret", "HOOKTRACK_SYNC_INTERVAL_HOURS": "0", "HOOKTRACK_BACKUP": "0",
           "YOUTUBE_API_KEY": "AIzaDEMOKEYNOTREAL00000000000000000000", "SOCIAVAULT_API_KEY": ""}
    for k in ("HOOKTRACK_PUBLIC_URL", "RESEND_API_KEY"):
        env.pop(k, None)
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{BASE}/api/public/legal", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("the app did not start")
        api = httpx.Client(base_url=BASE, headers={"origin": BASE}, timeout=60)
        r = api.post("/api/auth/signup", json={"email": EMAIL, "password": PASSWORD, "name": "Alex", "workspace_name": "Studio Sample", "timezone": "UTC"})
        r.raise_for_status()
        seed(url, api, r.json()["workspace"]["id"])

        log("2/4 the voice-over")
        scenes = make_voice(work, a.voice, a.rate)
        log("3/4 recording the app")
        timeline = record(scenes, work, out_dir)
        log("4/4 joining picture and voice")
        final = assemble(scenes, timeline, out_dir, a.name)
        (out_dir / "raw.webm").unlink(missing_ok=True)
        log(f"done: {final} ({duration(final):.0f}s)")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
