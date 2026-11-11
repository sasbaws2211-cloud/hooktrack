"""Records HookTrack's product demo: a narrated walkthrough of the real app, told as one person's journey.

    python tools/make_demo_video.py                       # writes tools/demo_out/hooktrack-demo.mp4 (+ .srt, a script and chapters)
    python tools/make_demo_video.py --voice en-US-AndrewNeural --out some/folder

The story: a title card, the landing page, signing up, adding an account, a week later (dashboard, the call on each account), hooks and
winners, the board, the calendar and scheduled posts, new accounts, Discover, a free tool, pricing, and an end card.

How it is made:
  1. a throwaway database holds an invented sample workspace (no real accounts, posts or customers) and the real app runs against it
     on its own port. The person in the video really signs up and adds an account in the browser; the rest of the week's data is added
     behind the scenes while that scene plays. Nothing touches your own database;
  2. each scene's narration is written with a neural voice (edge-tts, a free Microsoft service that needs internet; the script is public
     marketing copy). Its word timings are used so every on-screen action happens exactly when the narration mentions it;
  3. a real browser is driven through the app while the screen is recorded, with a visible cursor, a lower-third caption for each scene,
     a glowing ring on the thing being talked about, and a short fade between pages;
  4. ffmpeg joins picture and voice into an mp4 for YouTube.
Needs: Postgres (the dev one on 55434), Playwright with Chrome, ffmpeg on the PATH, and `pip install edge-tts`.
After uploading, the demo video id appears in static/demo-player.js, static/tool-cta.js, static/app.js and a few pages.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import subprocess
import sys
import threading
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
EMAIL, PASSWORD = "maya@studio-sample.example", "sample-workspace-pw-1"
W, H = 1280, 720
GAP = 0.6                      # seconds of breathing room after each line

# ---------------------------------------------------------------- the story
# key, caption (the lower third), the narration, and the beats: (when, action). `when` is a word or phrase in the narration (the action
# happens as it is spoken) or a number of seconds after the scene starts.
SCENES = [
    dict(key="open", cap="", chapter="Introduction",
         text="Most creators check their views, and guess what worked. HookTrack shows you what really did, and what to make next.",
         beats=[(0.0, "title"), ("shows you", "landing")]),
    dict(key="signup", cap="Create your workspace", chapter="Sign up",
         text="Getting started takes a minute. Create a workspace, and add the accounts you want to track.",
         beats=[("getting", "go_signup"), ("create", "type_signup"), ("track", "submit_signup")]),
    dict(key="add", cap="Add your accounts: TikTok, Instagram, YouTube", chapter="Add an account",
         text="Add an account, and HookTrack reads its videos automatically. It works with TikTok, Instagram and YouTube.",
         beats=[(0.2, "go_accounts"), ("reads", "open_add"), ("tiktok", "type_handle"), ("youtube", "save_account")]),
    dict(key="week", cap="Your week at a glance", chapter="The dashboard",
         text="A week later, the dashboard shows this week's views, the trend, and what to scale or stop.",
         beats=[(0.0, "dashboard"), ("views", "spot_views"), ("trend", "spot_trend"), ("scale", "spot_scale")]),
    dict(key="calls", cap="A clear call for every account", chapter="A clear call for every account",
         text="Every account gets a clear call. Three accounts, three platforms, all in one place.",
         beats=[(0.0, "movers"), ("three", "accounts_page")]),
    dict(key="hooks", cap="Every video, compared with its own account's normal", chapter="Hooks and winners",
         text="Every posted video is compared with its own account's normal. Tick winners only, to see the ones that beat it by a wide margin. "
              "Each hook gets a call: scale it, tweak it, or hold.",
         beats=[(0.0, "hooks_page"), ("compared", "spot_normal"), ("tick", "winners_only"), ("scale", "spot_decision")]),
    dict(key="board", cap="Idea, Scripted, Filmed, Posted", chapter="The board",
         text="Winners land on your board as cards, from idea to scripted, filmed and posted. Open a card to see the next step. "
              "Changes save on their own.",
         beats=[(0.0, "board_page"), ("scripted", "spot_board"), ("open", "open_card"), ("changes", "close_card")]),
    dict(key="publish", cap="Plan by day, publish on time", chapter="Calendar and scheduling",
         text="Switch to the calendar to plan by day. Cards with a clock are queued to publish at that time, "
              "and the scheduled list shows everything waiting.",
         beats=[(0.0, "calendar"), ("clock", "spot_clock"), ("scheduled", "scheduled_page")]),
    dict(key="newacct", cap="New accounts are compared with similar ones", chapter="New accounts",
         text="A brand new account has no history yet, so HookTrack compares its first videos with similar accounts, "
              "and counts down to its own baseline.",
         beats=[(0.0, "newacct_accounts"), ("similar", "newacct_hooks")]),
    dict(key="discover", cap="What's working in your niche", chapter="Discover",
         text="Discover finds recent videos that did several times better than their creator's usual, from creators your size. "
              "Make your own version with AI, or save the idea for later.",
         beats=[(0.0, "discover_page"), ("several", "spot_multiple"), ("make", "spot_make"), ("save", "save_idea")]),
    dict(key="tool", cap="Free hook scorer", chapter="Free tools",
         text="There are free tools too. The hook scorer rates your opening line out of one hundred, and shows exactly what to fix.",
         beats=[(0.0, "tool_page"), ("rates", "tool_run"), ("fix", "spot_fix")]),
    dict(key="pricing", cap="Start free. Upgrade when you need more.", chapter="Pricing",
         text="Start free with YouTube. Solo is twelve dollars a month, and Pro is thirty nine. AI help is included on every plan. "
              "Or take the lifetime deal: one payment, while the early supporter spots last.",
         beats=[(0.0, "pricing_page"), ("free", "spot_card0"), ("solo", "spot_card1"), ("pro", "spot_card2"), ("lifetime", "spot_card3")]),
    dict(key="end", cap="", chapter="Start free",
         text="HookTrack. Know what to make next. Start free at hooktrack dot site.",
         beats=[(0.0, "endcard")]),
]

if os.environ.get("DEMO_SCENES"):                       # debugging: run only these scenes, e.g. DEMO_SCENES=open,signup
    _only = set(os.environ["DEMO_SCENES"].split(","))
    SCENES[:] = [sc for sc in SCENES if sc["key"] in _only]

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


def workspace_of(url: str, email: str) -> int:
    db = psycopg2.connect(url)
    cur = db.cursor()
    cur.execute("SELECT m.workspace_id FROM memberships m JOIN users u ON u.id = m.user_id WHERE u.email=%s", (email,))
    ws = cur.fetchone()[0]
    db.close()
    return ws


def give_plan(url: str, ws: int) -> None:
    """The sample workspace is on a paid plan, so nothing in the walkthrough is blocked by a trial or a limit."""
    db = psycopg2.connect(url)
    db.autocommit = True
    d = db.cursor()
    d.execute("INSERT INTO platform_settings(key, value) VALUES ('billing_enabled', 'true') ON CONFLICT (key) DO UPDATE SET value='true'")
    d.execute("INSERT INTO workspace_subscriptions(workspace_id, plan_code, status) VALUES (%s,'pro','comp') "
              "ON CONFLICT (workspace_id) DO UPDATE SET plan_code='pro', status='comp'", (ws,))
    db.close()


def seed(url: str, ws: int) -> None:
    """The rest of the week: other accounts, a run of posts and daily views, scores, a fuller board, scheduled posts, Discover items."""
    api = httpx.Client(base_url=BASE, headers={"origin": BASE}, timeout=60)
    api.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).raise_for_status()
    have = {a["handle"]: a["id"] for a in api.get("/api/accounts").json()}
    db = psycopg2.connect(url)
    db.autocommit = True
    d = db.cursor()
    today = date.today()
    hook_ids = {}
    for handle, platform, niche, usual, hooks in ACCOUNTS:
        aid = have.get(handle) or api.post("/api/accounts", json={"handle": handle, "platform": platform, "niche": niche, "sync_enabled": False}).json()["id"]
        d.execute("UPDATE accounts SET niche=%s WHERE id=%s", (niche, aid))
        d.execute("INSERT INTO account_snapshots(account_id, workspace_id, day, followers) VALUES (%s,%s,current_date,%s)", (aid, ws, 48000))
        for i, v in enumerate(usual):
            d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at) "
                      "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now())", (ws, aid, today - timedelta(days=14 + i * 3), f"older post {i + 1}", v, f"{handle}-old{i}"))
        for text, views, ago in hooks:
            d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at, comments, saves, shares) "
                      "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now(), %s,%s,%s) RETURNING id",
                      (ws, aid, today - timedelta(days=ago), text, views, f"{handle}-{ago}", views // 900, views // 120, views // 400))
            hook_ids[text] = d.fetchone()[0]
    handle, platform, niche, posts = NEWBIE
    nid = api.post("/api/accounts", json={"handle": handle, "platform": platform, "niche": niche, "sync_enabled": False}).json()["id"]
    d.execute("INSERT INTO account_snapshots(account_id, workspace_id, day, followers) VALUES (%s,%s,current_date,%s)", (nid, ws, 420))
    for text, views, ago in posts:
        d.execute("INSERT INTO posts (workspace_id, account_id, post_date, status, hook, views, external_id, intent, views_updated_at) "
                  "VALUES (%s,%s,%s,'posted',%s,%s,%s,false, now())", (ws, nid, today - timedelta(days=ago), text, views, f"{handle}-{ago}"))
    # a believable run of daily views. Last week is steady, this week (the days so far) is clearly up, older weeks are lower: so the
    # dashboard shows growth, one account earns SCALE, and the weekly chart is not flat.
    monday = today - timedelta(days=today.weekday())
    so_far = today.weekday() + 1
    daily = {"@gymbrand": (6500, 11000), "@dailydiy": (2100, 3600), "@studio.shorts": (420, 800), "@newbie.fit": (40, 70)}
    d.execute("SELECT id, handle FROM accounts WHERE workspace_id=%s", (ws,))
    for aid, h in d.fetchall():
        base, last_week = daily.get(h, (100, 200))
        for back in range(0, 63):
            day = today - timedelta(days=back)
            wobble = 0.88 + ((back * 37) % 24) / 100.0
            v = last_week * 7 * 1.5 / so_far * wobble if day >= monday else (last_week * wobble if day >= monday - timedelta(days=7) else base * wobble)
            d.execute("INSERT INTO daily_views(account_id, workspace_id, day, views, source) VALUES (%s,%s,%s,%s,'manual') ON CONFLICT DO NOTHING", (aid, ws, day, int(v)))
    win_day = monday if (today - monday).days >= 2 else today - timedelta(days=5)      # a top hook this week, old enough to be judged
    d.execute("UPDATE posts SET post_date=%s WHERE workspace_id=%s AND hook='the aesthetic of consistency'", (win_day, ws))
    for text, v, sc, c in (("the aesthetic of consistency", 9, 9, 9), ("your 3-step morning routine", 8, 8, 8), ("3 things I stopped buying for my flat", 9, 8, 9),
                           ("how I study in 20 minutes a day", 9, 8, 8), ("new drop, link in bio", 4, 4, 5), ("wait for the transition", 6, 6, 5)):
        d.execute("UPDATE posts SET v=%s, s=%s, c=%s, score_by='manual' WHERE workspace_id=%s AND hook=%s", (v, sc, c, ws, text))
    for text in ("the aesthetic of consistency", "your 3-step morning routine", "3 things I stopped buying for my flat"):
        api.post(f"/api/posts/{hook_ids[text]}/followup")
    first = have.get("@gymbrand") or api.get("/api/accounts").json()[0]["id"]
    board = {}
    for status, hook, offset in (("idea", "Three mistakes new lifters make", 6), ("idea", "What a realistic week of meal prep looks like", 7),
                                 ("scripted", "Behind the scenes: how we plan a week of posts", 2), ("filmed", "What I eat before a morning session", 1),
                                 ("filmed", "The 10-minute warm-up I never skip", 3)):
        board[hook] = api.post("/api/posts", json={"account_id": first, "post_date": (today + timedelta(days=offset)).isoformat(), "status": status,
                                                   "hook": hook, "views": 0, "comments": 0, "intent": False}).json()["id"]
    # two filmed posts are queued to publish: they get a clock on the board and appear in the Scheduled list
    for i, hook in enumerate(("What I eat before a morning session", "The 10-minute warm-up I never skip")):
        pid = board[hook]
        d.execute("UPDATE posts SET scheduled_for = date_trunc('day', now()) + interval '1 day' * %s + interval '18 hours 15 minutes' WHERE id=%s", (i + 1, pid))
        d.execute("INSERT INTO publish_jobs(workspace_id, post_id, provider, status, scheduled_for) VALUES (%s,%s,'late','queued', "
                  "date_trunc('day', now()) + interval '1 day' * %s + interval '18 hours 15 minutes')", (ws, pid, i + 1))
    for band, p25, med, p75 in (("under-1k", 231, 554, 764), ("1k-10k", 596, 1552, 11906), ("10k-100k", 7814, 19710, 34186), ("100k-plus", 65794, 159516, 257709)):
        d.execute("INSERT INTO peer_benchmarks(platform, fmt, niche, band, source, n_accounts, n_posts, p25, median, p75) "
                  "VALUES ('tiktok','main','general',%s,'seed',20,300,%s,%s,%s) ON CONFLICT DO NOTHING", (band, p25, med, p75))
    for vid, niche, band, title, views, usual, subs, ago in DISCOVER:
        d.execute("INSERT INTO discover_items(platform, video_id, niche, band, channel_id, channel_title, title, duration_s, published_at, views, "
                  "channel_median, multiple, subscribers) VALUES ('youtube',%s,%s,%s,'UC0000000000000000000001','Sample creator',%s,30,now() - make_interval(days => %s),%s,%s,%s,%s)",
                  (vid, niche, band, title, ago, views, usual, round(views / usual, 2), subs))
    db.close()


# ---------------------------------------------------------------- the voice, with word timings
async def _speak(text: str, voice: str, path: Path, rate: str) -> list:
    import edge_tts
    words = []
    with open(path, "wb") as f:
        async for ch in edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary").stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["offset"] / 1e7, ch["text"]))
    return words


def duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return float(out.strip())


def make_voice(work: Path, voice: str, rate: str) -> None:
    for sc in SCENES:
        p = work / f"{sc['key']}.mp3"
        sc["audio"] = p
        sc["words"] = asyncio.run(_speak(sc["text"], voice, p, rate))
        sc["dur"] = duration(p)
        log(f"  voice {sc['key']}: {sc['dur']:.1f}s ({len(sc['words'])} words)")


def when_seconds(sc: dict, when) -> float:
    """When a beat fires, in seconds from the start of the scene's narration."""
    if isinstance(when, (int, float)):
        return float(when)
    toks = [(t, re.sub(r"[^a-z0-9']+", "", w.lower())) for t, w in sc["words"]]
    target = re.sub(r"[^a-z0-9' ]+", "", str(when).lower()).split()
    for i in range(len(toks)):
        if [x[1] for x in toks[i:i + len(target)]] == target:
            return max(0.0, toks[i][0] - 0.35)         # a touch early, so the picture leads the word
    log(f"    ! '{when}' not found in the {sc['key']} narration: firing at the middle")
    return sc["dur"] / 2


# ---------------------------------------------------------------- the look: cursor, captions, spotlight, fades
OVERLAY_JS = r"""
(() => {
  const run = () => {
  const h = document.documentElement;
  if (!window.__demoInit) {
    window.__demoInit = true;
    h.style.opacity = '0'; h.style.transition = 'opacity .4s ease';
    const fade = () => setTimeout(() => { h.style.opacity = '1'; }, 250);
    if (document.readyState === 'complete') fade(); else window.addEventListener('load', fade);
  }
  const css = `#ai-badge,#setup-progress,#trialbar,#verifybar,#coach,.coach,#ht-cookie,#guide-fab,#guide-panel{display:none!important}
    #demo-cursor{position:fixed;left:-60px;top:-60px;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:50%;background:rgba(79,70,229,.28);border:2px solid #4f46e5;box-shadow:0 2px 8px rgba(0,0,0,.25);z-index:2147483646;pointer-events:none;transition:transform .12s}
    #demo-cap{position:fixed;left:50%;bottom:34px;transform:translate(-50%,12px);max-width:760px;padding:13px 24px;border-radius:14px;background:rgba(14,17,30,.93);color:#fff;font:600 21px/1.3 system-ui,-apple-system,"Segoe UI",sans-serif;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,.35);opacity:0;transition:opacity .35s,transform .35s;z-index:2147483645;pointer-events:none}
    #demo-cap.on{opacity:1;transform:translate(-50%,0)}
    #demo-ring{position:fixed;border:3px solid #f59e0b;border-radius:12px;box-shadow:0 0 0 4px rgba(245,158,11,.25),0 0 28px rgba(245,158,11,.55);z-index:2147483644;pointer-events:none;opacity:0;transition:opacity .3s;animation:demoPulse 1.4s ease-in-out infinite}
    #demo-ring.on{opacity:1}
    @keyframes demoPulse{0%,100%{box-shadow:0 0 0 4px rgba(245,158,11,.25),0 0 22px rgba(245,158,11,.45)}50%{box-shadow:0 0 0 8px rgba(245,158,11,.18),0 0 34px rgba(245,158,11,.7)}}`;
  const add = () => {
    if (document.getElementById('demo-style')) return;
    const s = document.createElement('style'); s.id = 'demo-style'; s.textContent = css; (document.head || h).appendChild(s);
    const c = document.createElement('div'); c.id = 'demo-cursor'; h.appendChild(c);
    const cap = document.createElement('div'); cap.id = 'demo-cap'; h.appendChild(cap);
    const ring = document.createElement('div'); ring.id = 'demo-ring'; h.appendChild(ring);
    document.addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    document.addEventListener('mousedown', () => { c.style.transform = 'scale(.7)'; }, true);
    document.addEventListener('mouseup', () => { c.style.transform = 'scale(1)'; }, true);
    let target = null, pad = 8;
    const follow = () => {
      if (target && target.isConnected) {
        const r = target.getBoundingClientRect();
        ring.style.left = (r.left - pad) + 'px'; ring.style.top = (r.top - pad) + 'px';
        ring.style.width = (r.width + pad * 2) + 'px'; ring.style.height = (r.height + pad * 2) + 'px';
      } else ring.classList.remove('on');
      requestAnimationFrame(follow);
    };
    requestAnimationFrame(follow);
    window.__demo = {
      cap: t => { if (t) { cap.textContent = t; requestAnimationFrame(() => cap.classList.add('on')); } else cap.classList.remove('on'); },
      spot: (el, p) => { target = el; pad = p == null ? 8 : p; ring.classList.add('on'); },
      unspot: () => { target = null; ring.classList.remove('on'); },
    };
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
  };
  // an init script runs before the page has an <html> element: wait for it
  if (document.documentElement) run(); else { const t = setInterval(() => { if (document.documentElement) { clearInterval(t); run(); } }, 1); }
})();
"""

CARD_CSS = """
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{display:grid;place-items:center;background:radial-gradient(1100px 600px at 20% 10%,#2a2377 0%,transparent 60%),radial-gradient(900px 500px at 85% 90%,#0e5a52 0%,transparent 55%),#0a0d1a;
  color:#fff;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;text-align:center;overflow:hidden}
.card{padding:0 60px;animation:in .9s ease both}@keyframes in{from{opacity:0;transform:translateY(14px) scale(.98)}to{opacity:1;transform:none}}
.logo{width:74px;height:74px;margin:0 auto 26px;border-radius:20px;background:linear-gradient(135deg,#6366f1,#8b5cf6);display:grid;place-items:center;box-shadow:0 14px 40px rgba(99,102,241,.5)}
.logo svg{width:42px;height:42px}
h1{font-size:64px;line-height:1.08;margin:0 0 18px;letter-spacing:-.02em}h1 span{background:linear-gradient(90deg,#a5b4fc,#5eead4);-webkit-background-clip:text;color:transparent}
p{font-size:24px;color:#b9c2db;margin:0 auto;max-width:760px;line-height:1.4}
.cta{display:inline-block;margin-top:34px;padding:16px 34px;border-radius:999px;background:linear-gradient(90deg,#6366f1,#8b5cf6);font-size:24px;font-weight:700;box-shadow:0 12px 34px rgba(99,102,241,.5)}
"""
LOGO = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 18c4 0 5-4 7-8s4-4 7-4"/><circle cx="19" cy="6" r="1.6" fill="#fff"/></svg>'


def card_html(title: str, sub: str, cta: str = "") -> str:
    return (f"<!doctype html><meta charset=utf-8><style>{CARD_CSS}</style><div class=card><div class=logo>{LOGO}</div><h1>{title}</h1><p>{sub}</p>"
            + (f'<div class=cta>{cta}</div>' if cta else "") + "</div>")


TITLE = card_html("Find the hooks that <span>actually sell</span>", "See which videos beat each account's own normal, and what to make next.")
END = card_html("Know what to make <span>next</span>", "TikTok · Instagram Reels · YouTube Shorts", "Start free · hooktrack.site")


# ---------------------------------------------------------------- driving the browser
class Driver:
    def __init__(self, page, url: str):
        self.page, self.url = page, url
        self.seeder = None

    def wait_until(self, t: float) -> None:
        while True:
            left = t - time.monotonic()
            if left <= 0:
                return
            self.page.wait_for_timeout(int(min(200, left * 1000)))

    # --- on-screen helpers
    def cap(self, text: str) -> None:
        try:
            self.page.evaluate("t => window.__demo && window.__demo.cap(t)", text)
        except Exception:
            pass

    def spot(self, selector: str, nth: int = 0, pad: int = 8) -> bool:
        loc = self.page.locator(selector).nth(nth)
        try:
            loc.evaluate("(e, p) => { const r = e.getBoundingClientRect(); if (r.top < 70 || r.bottom > innerHeight - 90) e.scrollIntoView({block: 'center', behavior: 'smooth'}); window.__demo.spot(e, p); }", pad, timeout=2500)
            return True
        except Exception:
            return False

    def unspot(self) -> None:
        try:
            self.page.evaluate("window.__demo && window.__demo.unspot()")
        except Exception:
            pass

    # --- the pointer
    def glide(self, x: float, y: float, steps: int = 26) -> None:
        self.page.mouse.move(x, y, steps=steps)

    def to(self, selector: str, nth: int = 0, steps: int = 26, dx: float = 0, dy: float = 0) -> bool:
        loc = self.page.locator(selector).nth(nth)
        try:
            loc.evaluate("e => { const r = e.getBoundingClientRect(); if (r.top < 70 || r.bottom > innerHeight - 90) e.scrollIntoView({block: 'center'}); }", timeout=2500)
            self.page.wait_for_timeout(200)
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
        self.page.wait_for_timeout(220)
        self.page.mouse.down(); self.page.wait_for_timeout(70); self.page.mouse.up()
        return True

    def type(self, selector: str, text: str, delay: int = 55) -> None:
        if self.click(selector):
            self.page.keyboard.type(text, delay=delay)

    # --- moving between pages
    def goto(self, path: str) -> None:
        try:
            self.page.evaluate("document.documentElement.style.opacity = '0'")
            self.page.wait_for_timeout(330)
        except Exception:
            pass
        self.page.goto(f"{BASE}{path}")
        self.page.wait_for_timeout(900)

    def nav(self, tab: str) -> None:
        """Click the real navigation link, as a person would."""
        self.unspot()
        if not self.click(f"#nav a[data-tab='{tab}']"):
            self.goto(f"/app#/{tab}")
        self.page.wait_for_timeout(1000)


# ---- the actions the beats name
def a_title(d):
    d.page.set_content(TITLE)


def a_landing(d):
    d.goto("/welcome")
    d.cap("Find the hooks that actually sell")
    d.glide(640, 330, 22)


def a_go_signup(d):
    if not d.click("a:has-text('Get started') >> visible=true", nth=1):
        d.click("a:has-text('Get started')")
    d.page.wait_for_selector("input[name=email]", timeout=15000)
    d.page.wait_for_timeout(500)


def a_type_signup(d):
    d.type("input[name=name]", "Maya Johnson", 50)
    d.type("input[name=workspace_name]", "Maya Studio", 50)
    d.type("input[name=email]", EMAIL, 28)
    d.type("input[name=password]", PASSWORD, 28)


def a_submit_signup(d):
    d.click("button:has-text('Create workspace')")
    d.page.wait_for_selector("#nav", timeout=20000)
    give_plan(d.url, workspace_of(d.url, EMAIL))           # the sample workspace is on a paid plan from here on
    d.page.wait_for_timeout(1500)
    d.spot("a.btn:has-text('Add accounts')", 0, 10)


def a_go_accounts(d):
    d.unspot()
    if not d.click("a.btn:has-text('Add accounts')"):
        d.nav("accounts")
    d.page.wait_for_timeout(1000)


def a_open_add(d):
    d.click("button:has-text('+ Account')")
    d.page.wait_for_selector("#h", timeout=8000)
    d.page.wait_for_timeout(300)


def a_type_handle(d):
    d.type("#h", "@gymbrand", 70)
    d.spot("#pf", 0, 6)


def a_save_account(d):
    d.unspot()
    d.click("#save")
    d.page.wait_for_timeout(1200)
    d.seeder = threading.Thread(target=seed, args=(d.url, workspace_of(d.url, EMAIL)), daemon=True)   # the rest of the week, behind the scenes
    d.seeder.start()


def a_dashboard(d):
    if d.seeder:
        d.seeder.join(timeout=60)
    d.unspot()
    d.page.goto(f"{BASE}/app#/dashboard")
    d.page.reload()
    d.page.wait_for_selector("h1", timeout=20000)
    d.page.wait_for_timeout(1500)
    d.glide(520, 330, 24)


def a_spot_views(d):
    d.spot(".kpis .card, .grid.kpis .card", 0, 6)


def a_spot_trend(d):
    d.spot(".card:has-text('Views, last 8 weeks')", 0, 6)


def a_spot_scale(d):
    d.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
    d.page.wait_for_timeout(500)
    d.spot(".kpis .card, .grid.kpis .card", 2, 6)


def a_movers(d):
    d.unspot()
    d.page.evaluate("window.scrollTo({top: 560, behavior: 'smooth'})")
    d.page.wait_for_timeout(900)
    d.spot(".card:has-text('Week movers')", 0, 6)


def a_accounts_page(d):
    d.nav("accounts")
    d.page.wait_for_timeout(600)
    d.spot("table", 0, 4)


def a_hooks_page(d):
    d.nav("hooks")
    d.page.wait_for_timeout(500)


def a_spot_normal(d):
    d.spot("th:has-text('normal')", 0, 6)


def a_winners_only(d):
    d.unspot()
    d.click("text=Winners only")
    d.page.wait_for_timeout(900)
    d.spot("table tbody tr", 0, 4)


def a_spot_decision(d):
    d.spot("td .pill:has-text('Scale')", 0, 8)


def a_board_page(d):
    d.nav("pipeline")
    d.page.wait_for_timeout(800)


def a_spot_board(d):
    d.spot(".pipe-card", 0, 6)


def a_open_card(d):
    d.unspot()
    d.click(".pipe-card", nth=1)
    d.page.wait_for_timeout(900)


def a_close_card(d):
    d.spot("#savestate", 0, 14)
    d.page.wait_for_timeout(1200)
    d.unspot()
    d.page.keyboard.press("Escape")
    d.page.wait_for_timeout(500)


def a_calendar(d):
    d.nav("pipeline")
    d.click("button:has-text('Calendar'), a:has-text('Calendar')")
    d.page.wait_for_timeout(1200)


def a_spot_clock(d):
    d.spot("text=\U0001F4C5", 0, 8)


def a_scheduled_page(d):
    d.unspot()
    d.click("#section-strip a:has-text('Scheduled')")
    d.page.wait_for_timeout(1000)
    d.spot("table tbody tr", 0, 4)


def a_newacct_accounts(d):
    d.nav("accounts")
    d.page.wait_for_timeout(500)
    d.spot("text=posts to its own baseline", 0, 8)


def a_newacct_hooks(d):
    d.nav("hooks")
    d.page.wait_for_timeout(500)
    d.page.evaluate("window.scrollTo({top: 260, behavior: 'smooth'})")
    d.page.wait_for_timeout(700)
    d.spot("text=vs peers", 0, 10)


def a_discover_page(d):
    d.nav("discover")
    d.page.wait_for_timeout(500)
    d.to("#db"); d.page.select_option("#db", "")
    d.page.wait_for_timeout(500)
    d.to("#dn"); d.page.select_option("#dn", "fitness")
    d.page.wait_for_timeout(700)


def a_spot_multiple(d):
    d.spot(".disc-card .pill", 1, 8)


def a_spot_make(d):
    d.unspot()
    d.to("[data-mk]", nth=1)
    d.spot("[data-mk]", 1, 8)


def a_save_idea(d):
    d.unspot()
    d.click("[data-sv]", nth=1)


def a_tool_page(d):
    d.goto("/tools/video-hook-scorer")
    d.page.wait_for_timeout(300)
    d.glide(520, 400, 22)


def a_tool_run(d):
    d.click("#hk-sample")
    d.page.wait_for_timeout(900)


def a_spot_fix(d):
    d.page.evaluate("window.scrollTo({top: 420, behavior: 'smooth'})")
    d.page.wait_for_timeout(800)
    d.spot(".hk-card .hk-fail", 0, 8) or d.spot(".hk-card", 0, 6)


def a_pricing_page(d):
    d.goto("/welcome")
    d.page.evaluate("document.querySelector('#pricing').scrollIntoView({behavior: 'smooth', block: 'start'})")
    d.page.wait_for_timeout(1300)


def _card(i):
    def f(d):
        d.spot("#plans .plan", i, 6)
        d.to("#plans .plan", nth=i, steps=18, dy=140)
    return f


def a_endcard(d):
    d.unspot()
    d.page.set_content(END)


ACTIONS = {name[2:]: fn for name, fn in list(globals().items()) if name.startswith("a_")}
ACTIONS.update(spot_card0=_card(0), spot_card1=_card(1), spot_card2=_card(2), spot_card3=_card(3))


# ---------------------------------------------------------------- the recording
def record(work: Path, out_dir: Path, url: str) -> list:
    from playwright.sync_api import sync_playwright
    timeline = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(work / "video"), record_video_size={"width": W, "height": H},
                                  locale="en-US", timezone_id="UTC", color_scheme="light")
        ctx.add_init_script(OVERLAY_JS)
        ctx.route("**/i.ytimg.com/**", lambda route: route.fulfill(body=fake_thumb(route.request.url), content_type="image/png"))
        page = ctx.new_page()
        t_video = time.monotonic()
        d = Driver(page, url)
        page.set_content(TITLE)
        page.wait_for_timeout(600)
        for sc in SCENES:
            t0 = time.monotonic()
            timeline.append((sc["key"], t0 - t_video, sc["dur"]))
            plan = sorted(((when_seconds(sc, w), fn) for w, fn in sc["beats"]), key=lambda x: x[0])
            if sc["cap"]:
                plan.insert(0, (0.5, "caption"))
            for at, name in plan:
                d.wait_until(t0 + at)
                try:
                    if name == "caption":
                        d.cap(sc["cap"])
                    else:
                        ACTIONS[name](d)
                except Exception as e:
                    log(f"    ! {sc['key']}/{name}: {type(e).__name__}: {str(e)[:110]}")
                    if os.environ.get("DEMO_DEBUG"):
                        try:
                            page.screenshot(path=str(out_dir / f"debug_{sc['key']}_{name}.png"))
                        except Exception:
                            pass
            d.wait_until(t0 + sc["dur"] + GAP)
            if sc["key"] != "end":
                d.cap("")
            log(f"  scene {sc['key']}: {time.monotonic() - t0:.1f}s (voice {sc['dur']:.1f}s)")
        page.wait_for_timeout(500)
        ctx.close()
        browser.close()
    vids = sorted((work / "video").glob("*.webm"))
    shutil.copy(vids[-1], out_dir / "raw.webm")
    return timeline


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


# ---------------------------------------------------------------- joining picture and voice
def srt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h):02d}:{int(m):02d}:{int(s):02d},{int((s % 1) * 1000):03d}"


def assemble(timeline: list, out_dir: Path, name: str) -> Path:
    start = max(0.0, timeline[0][1] - 0.3)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(out_dir / "raw.webm")]
    for sc in SCENES:
        cmd += ["-i", str(sc["audio"])]
    parts, labels = [], []
    for i, (key, t0, dur) in enumerate(timeline):
        ms = int(max(0, t0 - start) * 1000) + 120
        parts.append(f"[{i + 1}:a]adelay={ms}|{ms},volume=1.0[a{i}]")
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[aout]")
    final = out_dir / f"{name}.mp4"
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]", "-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", "-shortest", str(final)]
    subprocess.run(cmd, check=True)
    with open(out_dir / f"{name}.srt", "w", encoding="utf-8") as f:
        for i, ((key, t0, dur), sc) in enumerate(zip(timeline, SCENES), 1):
            a = t0 - start + 0.12
            f.write(f"{i}\n{srt_time(a)} --> {srt_time(a + dur)}\n{sc['text']}\n\n")
    (out_dir / f"{name}-script.txt").write_text("\n\n".join(f"[{sc['key']}] {sc['text']}" for sc in SCENES) + "\n", encoding="utf-8")
    with open(out_dir / f"{name}-chapters.txt", "w", encoding="utf-8") as f:       # paste into the YouTube description
        for (key, t0, dur), sc in zip(timeline, SCENES):
            t = max(0, t0 - start)
            f.write(f"{int(t // 60)}:{int(t % 60):02d} {sc['chapter']}\n")
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

    log("1/4 the voice-over")
    make_voice(work, a.voice, a.rate)
    log(f"    total narration {sum(sc['dur'] for sc in SCENES):.0f}s")
    log("2/4 the sample app")
    url = build_database()
    env = {**os.environ, "DATABASE_URL": url, "HOOKTRACK_SECRET": "demo-secret", "HOOKTRACK_SYNC_INTERVAL_HOURS": "0", "HOOKTRACK_BACKUP": "0",
           "YOUTUBE_API_KEY": "AIzaDEMOKEYNOTREAL00000000000000000000", "SOCIAVAULT_API_KEY": ""}
    for k in ("HOOKTRACK_PUBLIC_URL", "RESEND_API_KEY", "HOOKTRACK_EMAIL_FROM"):
        env[k] = ""         # present but empty: the app only reads .env for variables that are absent, so no real email can be sent
    env["HOOKTRACK_ALLOW_LOCAL_EMAIL"] = "0"
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
        log("3/4 recording the app")
        timeline = record(work, out_dir, url)
        log("4/4 joining picture and voice")
        final = assemble(timeline, out_dir, a.name)
        (out_dir / "raw.webm").unlink(missing_ok=True)
        log(f"done: {final} ({duration(final):.0f}s)")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
        shutil.rmtree(work, ignore_errors=True)
        try:
            c = psycopg2.connect(ADMIN_URL)
            c.autocommit = True
            c.cursor().execute(f'DROP DATABASE IF EXISTS "{DB}" WITH (FORCE)')
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
