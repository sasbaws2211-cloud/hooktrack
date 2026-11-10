"""The in-page demo player. Needs the app running on http://localhost:8000 and Playwright installed:
    python tests/demo_player_check.py
YouTube is replaced by a fake embed page that speaks the same message protocol, so no network is used for the video."""
import json, sys, time
from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
FAKE = """<!doctype html><meta charset=utf-8><script>
var s={playerState:-1,currentTime:0,duration:100,muted:false,volume:100,playbackRate:1,videoLoadedFraction:0.4},on=false;
function post(o){parent.postMessage(JSON.stringify(o),'*')}
addEventListener('message',function(e){var d;try{d=JSON.parse(e.data)}catch(x){return}
 if(d.event==='listening'){on=true;post({event:'onReady'});post({event:'initialDelivery',info:s});return}
 if(d.event!=='command')return; var f=d.func,a=d.args||[]; post({event:'log',cmd:f,args:a});
 if(f==='playVideo'){s.playerState=1}else if(f==='pauseVideo'){s.playerState=2}
 else if(f==='seekTo'){s.currentTime=a[0];if(s.playerState===0)s.playerState=1}
 else if(f==='mute'){s.muted=true}else if(f==='unMute'){s.muted=false}
 else if(f==='setVolume'){s.volume=a[0]}else if(f==='setPlaybackRate'){s.playbackRate=a[0]}
 post({event:'infoDelivery',info:s})});
setInterval(function(){if(!on)return;if(s.playerState===1){s.currentTime+=0.25*s.playbackRate;if(s.currentTime>=s.duration){s.currentTime=s.duration;s.playerState=0}}post({event:'infoDelivery',info:s})},250);
</script>"""
results = []
def ok(name, cond):
    results.append(cond); print(("PASS " if cond else "FAIL ") + name)
    if not cond: sys.exit(1)

def cmds(pg): return pg.evaluate("(window.__cmds||[]).map(c => c.cmd + (c.args && c.args.length ? ':' + c.args.join(',') : ''))")

def setup(ctx, url):
    pg = ctx.new_page()
    pg.route("**/fake-embed/**", lambda r: r.fulfill(body=FAKE, content_type="text/html"))
    pg.goto(url); pg.wait_for_timeout(900)
    try: pg.get_by_role("button", name="Got it").click(timeout=500)
    except Exception: pass
    pg.evaluate("""() => { window.__cmds = []; addEventListener('message', e => { try { const d = JSON.parse(e.data); if (d.event === 'log') window.__cmds.push(d); } catch (x) {} });
                   HTDemo.configure({embedBase: location.origin + '/fake-embed/'}); }""")
    return pg

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome")
    ctx = b.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    yt_requests = []
    pg.on("request", lambda r: yt_requests.append(r.url) if ("youtube" in r.url or "ytimg" in r.url or "googlevideo" in r.url) else None)
    pg.goto(BASE + "/"); pg.wait_for_timeout(1200)
    ok("before any click, nothing is requested from YouTube", yt_requests == [])
    ok("the landing page has a Watch the demo button", pg.locator("#hero-demo").count() == 1)
    ctx.clear_cookies(); pg.close()

    pg = setup(ctx, BASE + "/")
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.click("#hero-demo"); pg.wait_for_timeout(1200)
    ok("clicking the demo link opens the player and stays on the page", pg.url.rstrip("/") == BASE and pg.locator(".htd-overlay:not([hidden])").count() == 1)
    ok("the video frame is requested only after the click", "/fake-embed/SAprelI0bJ8" in pg.get_attribute(".htd-frame iframe", "src") and "enablejsapi=1" in pg.get_attribute(".htd-frame iframe", "src") and "controls=0" in pg.get_attribute(".htd-frame iframe", "src"))
    ok("the video starts by itself and the loading spinner goes away", pg.locator(".htd-spin[hidden]").count() == 1 and "playVideo" in cmds(pg))
    ok("nothing sits in front of the video: every point of it belongs to the YouTube frame", pg.evaluate("""() => { const r = document.querySelector('.htd-stage').getBoundingClientRect(); const f = document.querySelector('.htd-frame iframe'); let bad = 0;
        for (const fx of [0.02, 0.25, 0.5, 0.75, 0.98]) for (const fy of [0.02, 0.25, 0.5, 0.75, 0.98]) { const e = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy); if (e !== f) bad++; } return bad === 0; }"""))
    ok("the controls and the close button sit outside the video box", pg.evaluate("""() => { const st = document.querySelector('.htd-stage').getBoundingClientRect();
        return ['.htd-ctrl', '.htd-head', '.htd-close'].every(q => { const r = document.querySelector(q).getBoundingClientRect(); return r.bottom <= st.top + 1 || r.top >= st.bottom - 1; }); }"""))
    ok("the dialog is labelled and modal", pg.get_attribute(".htd-overlay", "role") == "dialog" and pg.get_attribute(".htd-overlay", "aria-modal") == "true")
    ok("duration and time are shown", pg.inner_text(".htd-time").endswith("/ 1:40"))
    t0 = pg.inner_text(".htd-time"); pg.wait_for_timeout(1100)
    ok("the time moves while playing", pg.inner_text(".htd-time") != t0)
    ok("the pause button shows while playing", pg.get_attribute(".htd-bar .htd-ic", "aria-label") == "Pause")

    pg.keyboard.press("Space"); pg.wait_for_timeout(500)
    ok("space pauses", "pauseVideo" in cmds(pg) and pg.get_attribute(".htd-bar .htd-ic", "aria-label") == "Play")
    pg.keyboard.press("k"); pg.wait_for_timeout(400)
    ok("k plays again", cmds(pg).count("playVideo") >= 2)

    box = pg.locator(".htd-prog").bounding_box()
    pg.mouse.click(box["x"] + box["width"] * 0.5, box["y"] + box["height"] / 2); pg.wait_for_timeout(500)
    seeks = [c for c in cmds(pg) if c.startswith("seekTo")]
    ok("clicking the bar seeks to about the middle", abs(float(seeks[-1].split(":")[1].split(",")[0]) - 50) < 4)
    pg.keyboard.press("ArrowRight"); pg.wait_for_timeout(400)
    s2 = [c for c in cmds(pg) if c.startswith("seekTo")]
    ok("the right arrow skips ahead 5 seconds", float(s2[-1].split(":")[1].split(",")[0]) - float(seeks[-1].split(":")[1].split(",")[0]) > 3)
    pg.keyboard.press("ArrowLeft"); pg.wait_for_timeout(300)
    ok("the left arrow goes back", len([c for c in cmds(pg) if c.startswith("seekTo")]) > len(s2))
    ok("the progress bar is a labelled slider", pg.get_attribute(".htd-prog", "role") == "slider" and pg.get_attribute(".htd-prog", "aria-valuemax") == "100")

    pg.click(".htd-vol .htd-ic"); pg.wait_for_timeout(300)
    ok("the mute button mutes", "mute" in cmds(pg) and pg.get_attribute(".htd-vol .htd-ic", "aria-label") == "Unmute")
    pg.keyboard.press("m"); pg.wait_for_timeout(300)
    ok("m unmutes", "unMute" in cmds(pg))
    pg.fill(".htd-vol input", "40"); pg.wait_for_timeout(300)
    ok("the volume slider sets the volume", "setVolume:40" in cmds(pg))

    pg.click(".htd-spdbtn"); pg.wait_for_timeout(200)
    ok("the speed menu opens with six speeds", pg.locator(".htd-menu button").count() == 6 and pg.locator(".htd-menu:not([hidden])").count() == 1)
    pg.click(".htd-menu button[data-rate='1.5']"); pg.wait_for_timeout(500)
    ok("choosing 1.5x sets the speed and the label", "setPlaybackRate:1.5" in cmds(pg) and pg.inner_text(".htd-spdbtn").startswith("1.5"))

    pg.mouse.move(10, 10); pg.wait_for_timeout(3200)
    ok("the controls stay visible (they never cover the video, so they need not hide)", pg.locator(".htd-ctrl").is_visible())

    # the end screen
    pg.keyboard.press("End") if False else None
    pg.evaluate("document.querySelector('.htd-prog').focus()"); pg.keyboard.press("End"); pg.wait_for_timeout(1200)
    ok("at the end a call to action appears", pg.locator(".htd-end:not([hidden])").count() == 1 and "Ready to try it" in pg.inner_text(".htd-end"))
    ok("the call to action is below the video, not over it", pg.evaluate("document.querySelector('.htd-end').getBoundingClientRect().top >= document.querySelector('.htd-stage').getBoundingClientRect().bottom - 1"))
    href = pg.get_attribute(".htd-end a.htd-pri", "href")
    ok("its pricing link carries the demo tag", href == "/?ref=demo-home#pricing")
    pg.click(".htd-end [data-act=replay]"); pg.wait_for_timeout(700)
    ok("Watch again replays and hides the end screen", pg.locator(".htd-end[hidden]").count() == 1 and cmds(pg).count("playVideo") >= 3)

    pg.keyboard.press("Tab"); pg.keyboard.press("Tab")
    pg.click(".htd-stage"); pg.wait_for_timeout(300)
    ok("after clicking the video, focus returns to our window so Esc and Space still work", pg.evaluate("document.activeElement.tagName !== 'IFRAME'"))
    ok("keyboard focus stays inside the window", pg.evaluate("document.querySelector('.htd-overlay').contains(document.activeElement)"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(500)
    ok("Escape closes it, removes the video and returns focus to the button", pg.locator(".htd-overlay[hidden]").count() == 1 and pg.locator(".htd-frame iframe").count() == 0 and pg.evaluate("document.activeElement.id") == "hero-demo")
    ok("the page can scroll again", not pg.evaluate("document.documentElement.classList.contains('htd-lock')"))
    pg.click("#hero-demo"); pg.wait_for_timeout(800)
    pg.mouse.click(5, 5); pg.wait_for_timeout(500)
    ok("clicking the backdrop closes it, and it can be opened again", pg.locator(".htd-overlay[hidden]").count() == 1)
    ok("no page errors so far", errs == [])
    pg.close()

    # other places that carry the demo link
    pg = setup(ctx, BASE + "/tools/tiktok-analytics")
    pg.click("#sample"); pg.click("#go"); pg.wait_for_timeout(500)
    pg.click(".tool-actions a[href*='youtu.be']"); pg.wait_for_timeout(900)
    ok("a tool's result button opens the player", pg.locator(".htd-overlay:not([hidden])").count() == 1)
    pg.keyboard.press("End") if False else None
    pg.evaluate("document.querySelector('.htd-prog').focus()"); pg.keyboard.press("End"); pg.wait_for_timeout(1000)
    ok("the end screen on a tool page tags the pricing link with the page", pg.get_attribute(".htd-end a.htd-pri", "href") == "/?ref=demo-tools-tiktok-analytics#pricing")
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.click(".cta-box a[href*='youtu.be']"); pg.wait_for_timeout(700)
    ok("the closing box's button opens the player too", pg.locator(".htd-overlay:not([hidden])").count() == 1)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    ctrl_click = pg.evaluate("""() => { window.__seen = null; window.addEventListener('click', e => { window.__seen = e.defaultPrevented; e.preventDefault(); }, {once: true});
                                 const a = document.querySelector('.cta-box a[href*="youtu.be"]');
                                 a.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, ctrlKey: true})); return window.__seen; }""")
    ok("ctrl-click still opens YouTube in a new tab (not intercepted)", ctrl_click is False)
    pg.close()

    # the in-app button
    pg = ctx.new_page()
    pg.route("**/fake-embed/**", lambda r: r.fulfill(body=FAKE, content_type="text/html"))
    email = f"demo{int(time.time())}@example.com"
    r = pg.request.post(BASE + "/api/auth/signup", data={"email": email, "password": "Passw0rd!long1", "name": "D", "workspace_name": "Demo Co"})
    ok("a test account can sign up", r.status == 200)
    pg.goto(BASE + "/app#/dashboard"); pg.wait_for_timeout(2500)
    try: pg.get_by_role("button", name="Got it").click(timeout=500)
    except Exception: pass
    ok("a new workspace sees Watch the demo in its Get started card", pg.locator("#watch-demo").count() == 1)
    pg.evaluate("HTDemo.configure({embedBase: location.origin + '/fake-embed/'})")
    pg.click("#watch-demo"); pg.wait_for_timeout(1000)
    ok("in the app it opens the player", pg.locator(".htd-overlay:not([hidden])").count() == 1)
    pg.evaluate("document.querySelector('.htd-prog').focus()"); pg.keyboard.press("End"); pg.wait_for_timeout(1000)
    ok("in the app the end screen points to the plans", pg.get_attribute(".htd-end a.htd-pri", "href") == "/app#/billing" and "See plans" in pg.inner_text(".htd-end a.htd-pri"))
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.request.post(BASE + "/api/workspace/seed-demo?force=true"); pg.reload(); pg.wait_for_timeout(2500)
    ok("once there is data, the dashboard header has a Demo button too", pg.locator("#demo-dash").count() == 1)
    pg.click("#demo-dash"); pg.wait_for_timeout(900)
    ok("and it opens the player", pg.locator(".htd-overlay:not([hidden])").count() == 1)
    pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
    pg.close()

    # a phone
    ctx2 = b.new_context(viewport={"width": 390, "height": 800}, has_touch=True, is_mobile=True)
    pg = setup(ctx2, BASE + "/")
    pg.click("#hero-demo"); pg.wait_for_timeout(1200)
    r = pg.locator(".htd-stage").bounding_box()
    ok("on a phone the player fills the width and keeps 16:9", abs(r["width"] - 390) < 2 and abs(r["width"] / r["height"] - 16 / 9) < 0.05)
    ok("no sideways scrolling on a phone", not pg.evaluate("document.documentElement.scrollWidth > window.innerWidth + 2"))
    pg.close()

    # blocked or slow: a clear fallback to YouTube
    pg = b.new_context(viewport={"width": 1280, "height": 800}).new_page()
    pg.route("**/fake-embed/**", lambda r: r.abort())
    pg.goto(BASE + "/"); pg.wait_for_timeout(900)
    try: pg.get_by_role("button", name="Got it").click(timeout=500)
    except Exception: pass
    pg.evaluate("HTDemo.configure({embedBase: location.origin + '/fake-embed/'})")
    pg.click("#hero-demo"); pg.wait_for_timeout(9800)
    ok("if the video cannot load, a link to YouTube is shown", pg.locator(".htd-err:not([hidden])").count() == 1 and pg.get_attribute(".htd-err a", "href") == "https://youtu.be/SAprelI0bJ8")
    pg.close(); b.close()
print("ALL OK (%d)" % len(results))
