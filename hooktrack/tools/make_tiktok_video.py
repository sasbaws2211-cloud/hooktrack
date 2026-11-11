"""Records a short vertical (9:16, 1080x1920) TikTok video for HookTrack: the "3x your normal" rule, then the free hook scorer.

    python tools/make_tiktok_video.py                 # writes tools/demo_out/hooktrack-tiktok.mp4 (+ a caption text file)
    python tools/make_tiktok_video.py --voice en-US-AndrewNeural --out some/folder

It reuses the demo tool (make_demo_video.py): the same throwaway sample database, neural voice with word timings, on-screen cursor,
captions and spotlight. Nothing touches your own database. Needs the same things: Postgres on 55434, Playwright with Chrome, ffmpeg,
and `pip install edge-tts`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_demo_video as demo                                   # noqa: E402

demo.SCENES.clear()
VW, VH = 400, 711                                                # laid out like a phone, saved at 1080x1920

SCENES = [
    dict(key="hook", cap="", text="Stop asking if your video got enough views. Ask this instead.",
         beats=[(0.0, "card_hook")]),
    dict(key="rule", cap="", text="Take your last ten videos, find the middle number of views, and divide each one by it. "
                                  "Three times or more? A real winner.",
         beats=[(0.0, "card_rule0"), ("take", "card_rule1"), ("divide", "card_rule2"), ("three", "card_rule3")]),
    dict(key="winners", cap="Your winners: 3× your normal", text="Now look at only your winners, and ask: what did the first line say? That's your hook.",
         beats=[(0.0, "winners_page"), ("only", "winners_only"), ("first", "spot_winner")]),
    dict(key="tool", cap="Free hook scorer", pad=1.8, text="Then score your hook before you post. A weak one scores low. A stronger one jumps. Free.",
         beats=[(0.0, "tool_page"), ("weak", "tool_weak"), ("stronger", "tool_strong")]),
    dict(key="end", cap="", text="Free hook scorer. Link in bio.", beats=[(0.0, "card_end")]),
]
CAPTION = ("Your views mean nothing without a baseline. Here's the 3x rule I use to find what actually worked. "
           "Free hook scorer in bio #tiktoktips #contentcreator #shortformvideo #youtubeshorts")
WEAK = "new drop, link in bio"
STRONG = "3 things I stopped buying for my flat"

CSS = """
*{box-sizing:border-box}html,body{margin:0;height:100%}
body{display:flex;flex-direction:column;justify-content:center;align-items:center;text-align:center;color:#fff;overflow:hidden;padding:0 34px;
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  background:radial-gradient(700px 500px at 15% 8%,#2a2377 0%,transparent 60%),radial-gradient(600px 420px at 90% 95%,#0e5a52 0%,transparent 55%),#0a0d1a}
.logo{width:56px;height:56px;margin-bottom:26px;border-radius:16px;background:linear-gradient(135deg,#6366f1,#8b5cf6);display:grid;place-items:center;box-shadow:0 10px 30px rgba(99,102,241,.5)}
.logo svg{width:32px;height:32px}
h1{font-size:54px;line-height:1.12;margin:0;letter-spacing:-.02em;animation:in .7s ease both}
h1 span{background:linear-gradient(90deg,#a5b4fc,#5eead4);-webkit-background-clip:text;color:transparent}
@keyframes in{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
.ln{font-size:42px;font-weight:700;line-height:1.2;margin:14px 0;opacity:0;transform:translateY(14px);transition:opacity .45s,transform .45s}
.ln.on{opacity:1;transform:none}.ln small{display:block;font-size:24px;font-weight:500;color:#b9c2db;margin-top:6px}
.big{font-size:110px;font-weight:800;color:#5eead4}.pill{display:inline-block;padding:8px 22px;border-radius:999px;background:linear-gradient(90deg,#6366f1,#8b5cf6);font-size:36px;font-weight:800}
.cta{margin-top:30px;padding:16px 30px;border-radius:999px;background:linear-gradient(90deg,#6366f1,#8b5cf6);font-size:30px;font-weight:700;box-shadow:0 10px 30px rgba(99,102,241,.5)}
p{font-size:22px;color:#b9c2db;margin:16px 0 0}
body>*{zoom:.74}
"""
LOGO = '<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 18c4 0 5-4 7-8s4-4 7-4"/><circle cx="19" cy="6" r="1.6" fill="#fff"/></svg>'


def page(body: str) -> str:
    return f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'><style>{CSS}</style>{body}"


HOOK = page(f'<div class=logo>{LOGO}</div><h1>Stop asking if your video got <span>enough views</span>.<br><br>Ask <span>this</span> instead.</h1>')
RULE = page('<div class=ln id=l1>Your last 10 videos<small>find the middle number of views</small></div>'
            '<div class=ln id=l2>Each video <b>÷</b> that number</div>'
            '<div class=ln id=l3><span class=big>3×</span><br>or more?</div>'
            '<div class=ln id=l4><span class=pill>a real winner</span></div>')
END = page(f'<div class=logo>{LOGO}</div><h1>Free <span>hook scorer</span></h1><div class=cta>Link in bio · hooktrack.site</div><p>No card needed</p>')


def show(d, *ids):
    d.page.evaluate("ids => ids.forEach(i => { const e = document.getElementById(i); if (e) e.classList.add('on'); })", list(ids))


def a_card_hook(d):
    d.page.set_content(HOOK)


def a_card_rule0(d):
    d.page.set_content(RULE)


def a_card_rule1(d):
    show(d, "l1")


def a_card_rule2(d):
    show(d, "l2")


def a_card_rule3(d):
    show(d, "l3", "l4")


def a_winners_page(d):
    d.goto("/app#/hooks")
    d.page.reload()
    d.page.wait_for_selector("h1", timeout=20000)
    d.page.wait_for_timeout(700)


def a_winners_only(d):
    d.click("text=Winners only")
    d.page.wait_for_timeout(800)


def a_spot_winner(d):
    d.spot("table tbody tr", 0, 4)


def a_tool_page(d):
    d.unspot()
    d.goto("/tools/video-hook-scorer")
    d.page.wait_for_timeout(400)


def _score(d, text):
    d.page.evaluate("document.querySelector('#hk-text').scrollIntoView({block: 'center'})")
    d.page.wait_for_timeout(350)
    d.to("#hk-text", steps=14)
    d.page.fill("#hk-text", "")
    d.page.focus("#hk-text")
    d.page.keyboard.type(text, delay=14)
    d.click("#hk-go")
    d.page.wait_for_timeout(700)
    d.page.evaluate("document.querySelector('#hk-out').scrollIntoView({block: 'center', behavior: 'smooth'})")


def a_tool_weak(d):
    _score(d, WEAK)


def a_tool_strong(d):
    _score(d, STRONG)


def a_card_end(d):
    d.unspot()
    d.page.set_content(END)


def _goto(self, path: str) -> None:
    """Move to another page behind a dark cover, so there is never a white flash between scenes."""
    try:
        self.page.evaluate("const c = document.getElementById('demo-cover'); if (c) c.style.opacity = '1'")
        self.page.wait_for_timeout(420)
    except Exception:
        pass
    self.page.goto(f"{demo.BASE}{path}")
    self.page.wait_for_timeout(900)


demo.Driver.goto = _goto
ACTIONS = {n[2:]: f for n, f in list(globals().items()) if n.startswith("a_")}


def prepare_workspace(url: str) -> None:
    """Sign up through the API, put the sample workspace on a paid plan and fill the week's data. Done before the camera rolls."""
    api = httpx.Client(base_url=demo.BASE, headers={"origin": demo.BASE}, timeout=60)
    api.post("/api/auth/signup", json={"name": "Maya Johnson", "workspace_name": "Maya Studio", "email": demo.EMAIL,
                                       "password": demo.PASSWORD}).raise_for_status()
    demo.give_plan(url, demo.workspace_of(url, demo.EMAIL))
    demo.seed(url, demo.workspace_of(url, demo.EMAIL))


def record(work: Path, out_dir: Path, url: str) -> list:
    from playwright.sync_api import sync_playwright
    old_fade = ("h.style.opacity = '0'; h.style.transition = 'opacity .4s ease';\n"
                "    const fade = () => setTimeout(() => { h.style.opacity = '1'; }, 250);")
    assert old_fade in demo.OVERLAY_JS
    cover = ("const cv = document.createElement('div'); cv.id = 'demo-cover'; cv.style.cssText = 'position:fixed;inset:0;background:#0a0d1a;"
             "z-index:2147483647;transition:opacity .4s ease;pointer-events:none'; h.appendChild(cv);\n"
             "    const fade = () => setTimeout(() => { cv.style.opacity = '0'; }, 250);")
    overlay = (demo.OVERLAY_JS.replace(old_fade, cover).replace("bottom:34px", "top:56px").replace("max-width:760px", "max-width:340px")
               .replace("font:600 21px", "font:700 20px").replace("translate(-50%,12px)", "translate(-50%,-12px)"))
    timeline = []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        t_ctx = time.monotonic()
        ctx = browser.new_context(viewport={"width": VW, "height": VH}, record_video_dir=str(work / "video"),
                                  record_video_size={"width": 1080, "height": 1920}, locale="en-US", timezone_id="UTC", color_scheme="light")
        ctx.add_init_script(overlay)
        ctx.route("**/i.ytimg.com/**", lambda route: route.fulfill(body=demo.fake_thumb(route.request.url), content_type="image/png"))
        pg = ctx.new_page()
        pg.goto(f"{demo.BASE}/welcome")
        pg.evaluate("([e, p]) => fetch('/api/auth/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({email: e, password: p})})",
                    [demo.EMAIL, demo.PASSWORD])
        pg.wait_for_timeout(500)
        d = demo.Driver(pg, url)
        pg.set_content(HOOK)
        pg.wait_for_timeout(500)
        t_video = time.monotonic()
        lead = t_video - t_ctx                                   # seconds of raw video before the first scene
        for sc in SCENES:
            t0 = time.monotonic()
            timeline.append((sc["key"], t0 - t_video, sc["dur"]))
            plan = sorted(((demo.when_seconds(sc, w), fn) for w, fn in sc["beats"]), key=lambda x: x[0])
            if sc["cap"]:
                plan.append((2.2, "caption"))                     # after the page has loaded, or the caption is wiped by the navigation
                plan.sort(key=lambda x: x[0])
            for at, name in plan:
                d.wait_until(t0 + at)
                try:
                    d.cap(sc["cap"]) if name == "caption" else ACTIONS[name](d)
                except Exception as e:
                    demo.log(f"    ! {sc['key']}/{name}: {type(e).__name__}: {str(e)[:110]}")
                    if os.environ.get("DEMO_DEBUG"):
                        pg.screenshot(path=str(out_dir / f"debug_{sc['key']}_{name}.png"))
            d.wait_until(t0 + sc["dur"] + sc.get("pad", 0.35))
            if sc["key"] != "end":
                d.cap("")
            demo.log(f"  scene {sc['key']}: {time.monotonic() - t0:.1f}s (voice {sc['dur']:.1f}s)")
        pg.wait_for_timeout(1000)
        ctx.close()
        browser.close()
    vids = sorted((work / "video").glob("*.webm"))
    shutil.copy(vids[-1], out_dir / "raw.webm")
    timeline.insert(0, ("_lead", lead, 0))
    return timeline


def assemble(timeline: list, out_dir: Path, name: str) -> Path:
    # the video clock starts when the context opens, so the trim point is the offset of the first scene in the raw file
    lead = timeline.pop(0)[1]
    start = max(0.0, lead - 0.4)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", str(out_dir / "raw.webm")]
    for sc in SCENES:
        cmd += ["-i", str(sc["audio"])]
    parts, labels = [], []
    for i, (key, t0, dur) in enumerate(timeline):
        ms = int(max(0, t0) * 1000) + 400
        parts.append(f"[{i + 1}:a]adelay={ms}|{ms}[a{i}]")
        labels.append(f"[a{i}]")
    parts.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0[aout]")
    final = out_dir / f"{name}.mp4"
    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]", "-vf", "scale=1080:1920:flags=lanczos",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", "-shortest", str(final)]
    subprocess.run(cmd, check=True)
    (out_dir / f"{name}-caption.txt").write_text(CAPTION + "\n\n" + "\n".join(f"[{s['key']}] {s['text']}" for s in SCENES) + "\n", encoding="utf-8")
    return final


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(demo.ROOT / "tools" / "demo_out"))
    ap.add_argument("--name", default="hooktrack-tiktok")
    ap.add_argument("--voice", default="en-US-AriaNeural")
    ap.add_argument("--rate", default="+4%")
    a = ap.parse_args()
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_work_tt"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    demo.SCENES[:] = SCENES                                          # make_voice works on demo.SCENES
    demo.log("1/4 the voice-over")
    demo.make_voice(work, a.voice, a.rate)
    demo.log(f"    total narration {sum(s['dur'] for s in SCENES):.0f}s")
    demo.log("2/4 the sample app")
    url = demo.build_database()
    env = {**os.environ, "DATABASE_URL": url, "HOOKTRACK_SECRET": "demo-secret", "HOOKTRACK_SYNC_INTERVAL_HOURS": "0", "HOOKTRACK_BACKUP": "0",
           "YOUTUBE_API_KEY": "AIzaDEMOKEYNOTREAL00000000000000000000", "SOCIAVAULT_API_KEY": ""}
    for k in ("HOOKTRACK_PUBLIC_URL", "RESEND_API_KEY", "HOOKTRACK_EMAIL_FROM"):
        env[k] = ""         # present but empty: the app only reads .env for variables that are absent, so no real email can be sent
    env["HOOKTRACK_ALLOW_LOCAL_EMAIL"] = "0"
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(demo.PORT)], cwd=demo.ROOT, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(80):
            try:
                if httpx.get(f"{demo.BASE}/api/public/legal", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            raise SystemExit("the app did not start")
        prepare_workspace(url)
        demo.log("3/4 recording")
        timeline = record(work, out_dir, url)
        demo.log("4/4 joining picture and voice")
        final = assemble(timeline, out_dir, a.name)
        (out_dir / "raw.webm").unlink(missing_ok=True)
        demo.log(f"done: {final} ({demo.duration(final):.0f}s)")
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
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
