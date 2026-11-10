"""Free YouTube plan: the official-API client, the sync, the free plan's limits and the YouTube-only rule.
Run: python tests/youtube_free_check.py (local test database; YouTube is faked, nothing leaves the machine)."""
import os, sys, time
from datetime import datetime, timedelta, timezone
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
KEY = "TESTKEYabcdefghijklmnop1234"
os.environ["YOUTUBE_API_KEY"] = KEY
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from fastapi.testclient import TestClient
from app import auth, billing, db, sociavault, youtube_api as yt
from app.main import app

db.init_schema()
billing._pset(auth.SETTING_SIGNUP_IP_DAILY, 1000)
db.execute("DELETE FROM rate_limits")
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))

# ---------------- a fake YouTube ----------------
CHAN = "UC" + "a" * 22
now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
VIDEOS = {
    "short000001": {"title": "Fast hook", "pub": now - timedelta(days=2), "dur": "PT45S", "views": "5400", "likes": "310", "comments": "12"},
    "long0000002": {"title": "Full tutorial", "pub": now - timedelta(days=3), "dur": "PT10M2S", "views": "900", "likes": "40", "comments": "3"},
    "old00000003": {"title": "Old one", "pub": now - timedelta(days=60), "dur": "PT30S", "views": "100", "likes": None, "comments": None},
}
calls = []
mode = {"fail": None}


def handler(request: httpx.Request) -> httpx.Response:
    name = request.url.path.rsplit("/", 1)[-1]
    q = dict(request.url.params)
    calls.append((name, q))
    if mode["fail"] == "quota":
        return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
    if mode["fail"] == "key":
        return httpx.Response(400, json={"error": {"errors": [{"reason": "keyInvalid"}]}})
    if q.get("key") != KEY:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})
    if name == "channels":
        handle = q.get("forHandle", "")
        if q.get("id") == CHAN or handle in ("@testchan", "@c1", "@c2", "@c3"):
            return httpx.Response(200, json={"items": [{"id": CHAN, "snippet": {"title": "Test Channel", "customUrl": handle or "@testchan", "description": "bio"},
                                                        "statistics": {"subscriberCount": "12345", "videoCount": "77"},
                                                        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + "a" * 22}}}]})
        return httpx.Response(200, json={"items": []})
    if name == "playlistItems":
        return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v}} for v in VIDEOS]})
    if name == "videos":
        want = [i for i in q.get("id", "").split(",") if i in VIDEOS]
        items = []
        for vid in want:
            v = VIDEOS[vid]
            st = {"viewCount": v["views"]}
            if v["likes"] is not None:
                st["likeCount"] = v["likes"]
            if v["comments"] is not None:
                st["commentCount"] = v["comments"]
            items.append({"id": vid, "snippet": {"title": v["title"], "description": v["title"] + " description", "publishedAt": iso(v["pub"]), "channelId": CHAN, "channelTitle": "Test Channel"},
                          "statistics": st, "contentDetails": {"duration": v["dur"]}})
        return httpx.Response(200, json={"items": items})
    return httpx.Response(404, json={})


yt._http = lambda: httpx.Client(transport=httpx.MockTransport(handler))

# ---------------- the client ----------------
ok("durations parse", yt.parse_duration("PT45S") == 45 and yt.parse_duration("PT1M5S") == 65 and yt.parse_duration("PT10M2S") == 602 and yt.parse_duration("P0D") is None and yt.parse_duration("") is None)
ok("3 minutes or less is short-form", yt.is_short_form(180) and yt.is_short_form(45) and not yt.is_short_form(181) and not yt.is_short_form(None))
ok("video ids come out of links", yt.video_id_from_url("https://youtu.be/abcDEF12345") == "abcDEF12345" and yt.video_id_from_url("https://www.youtube.com/watch?v=abcDEF12345&t=3") == "abcDEF12345" and yt.video_id_from_url("https://www.youtube.com/shorts/abcDEF12345") == "abcDEF12345" and yt.video_id_from_url("https://example.com") == "")
ok("the uploads playlist id needs no call", yt.uploads_playlist_for(CHAN) == "UU" + "a" * 22 and yt.uploads_playlist_for("nope") == "")
ok("the key is read from the environment", yt.available() and yt.status()["source"] == "environment" and KEY not in str(yt.status()))

db.execute("DELETE FROM youtube_quota")
before = yt.used_today()
ch = yt.channel(handle="@testchan")
ok("a channel is read by handle", ch["id"] == CHAN and ch["title"] == "Test Channel" and ch["subscribers"] == 12345 and ch["uploads_playlist"].startswith("UU"))
ok("each call costs one unit", yt.used_today() == before + 1)
try:
    yt.channel(handle="@missing"); notfound = False
except yt.YouTubeError as e:
    notfound = e.status == 404
ok("a missing channel raises 404", notfound)
lk = yt.lookup("@missing"); ok("lookup of a missing channel says not found, not an error", lk["found"] is False)
lk = yt.lookup("@testchan"); ok("lookup of a real channel", lk["found"] and lk["display_name"] == "Test Channel" and lk["credits_est"] == 0 and lk["followers"] == 12345)
vs = {v["id"]: v for v in yt.videos(list(VIDEOS))}
ok("videos carry views, likes, comments and length", vs["short000001"]["views"] == 5400 and vs["short000001"]["likes"] == 310 and vs["short000001"]["comments"] == 12 and vs["short000001"]["duration_s"] == 45)
ok("hidden likes and comments are None, not zero", vs["old00000003"]["likes"] is None and vs["old00000003"]["comments"] is None)
vi = yt.video_info("https://youtu.be/short000001"); ok("a video link is read", vi["views"] == 5400 and vi["handle"] and vi["platform"] == "YouTube")

# quota: stop at the budget, and Google's own refusal pauses the day
budget = yt.budget()
db.execute("DELETE FROM youtube_quota"); db.execute("INSERT INTO youtube_quota(day, units) VALUES (%s,%s)", (yt._day(), budget - 1))
yt.channel(handle="@testchan")
try:
    yt.channel(handle="@testchan"); stopped = False
except yt.YouTubeError as e:
    stopped = e.status == 503 and "tomorrow" in str(e)
ok("calls stop at the daily budget with a clear message", stopped)
db.execute("DELETE FROM youtube_quota"); mode["fail"] = "quota"
try:
    yt.channel(handle="@testchan"); paused = False
except yt.YouTubeError as e:
    paused = e.status == 503
ok("Google's quota error pauses YouTube for the day", paused and yt.used_today() >= yt.GOOGLE_DAILY_LIMIT)
db.execute("DELETE FROM youtube_quota"); mode["fail"] = "key"
try:
    yt.channel(handle="@testchan"); msg = ""
except yt.YouTubeError as e:
    msg = str(e)
ok("a rejected key gives a safe message that never contains the key", "not set up correctly" in msg and KEY not in msg)
mode["fail"] = None; db.execute("DELETE FROM youtube_quota")

# ---------------- a workspace on the free plan ----------------
c = TestClient(app)
email = f"yt{int(time.time())}@example.com"
r = c.post("/api/auth/signup", json={"email": email, "password": "Passw0rd!long1", "name": "Y", "workspace_name": "YT Free"})
assert r.status_code == 200, r.text
ws = db.one("SELECT workspace_id FROM memberships m JOIN users u ON u.id=m.user_id WHERE u.email=%s", (email,))["workspace_id"]
db.execute("UPDATE users SET email_verified_at = now() WHERE email=%s", (email,))
billing.set_enabled(True)
db.execute("DELETE FROM platform_settings WHERE key IN ('free_plan')")
ok("the free plan is on by default when the Google key works", billing.free_plan_on())

st = billing.workspace_state(ws)
ok("a workspace with no plan is on the free plan, not locked out", st["state"] == "free" and not billing.access_blocked(ws))
p = billing.effective_plan(ws)
ok("the free plan: 3 channels, 1 member, 20 AI, no publishing, no credits", p["name"] == "Free" and p["max_accounts"] == 3 and p["max_members"] == 1 and p["max_ai_calls"] == 20 and p["max_connected"] == 0 and p["max_credits"] == 0)
b = c.get("/api/billing").json()
ok("the billing page reports the free state", b["state"] == "free" and b["plan"]["name"] == "Free" and b["limits"]["connected"] == 0 and b["free_plan"]["name"] == "Free")
pp = c.get("/api/public/pricing").json()
ok("public pricing offers the free plan", pp["free_plan"]["platforms"] == "YouTube" and pp["free_plan"]["max_accounts"] == 3)

r = c.post("/api/accounts", json={"handle": "@testchan", "platform": "YouTube"}); ok("a YouTube channel can be added", r.status_code == 200)
aid = r.json()["id"]
r = c.post("/api/accounts", json={"handle": "@tikkie", "platform": "TikTok"}); ok("a TikTok account is refused on the free plan", r.status_code == 402 and "YouTube" in r.text)
r = c.post("/api/accounts", json={"handle": "@instaq", "platform": "Instagram"}); ok("an Instagram account is refused on the free plan", r.status_code == 402)
r = c.post("/api/accounts/lookup", json={"handle": "@testchan", "platform": "YouTube"}); ok("a YouTube lookup works with no SociaVault key or credit", r.status_code == 200 and r.json()["found"] is True and r.json()["credits_est"] == 0)
r = c.post("/api/accounts/lookup", json={"handle": "@tikkie", "platform": "TikTok"}); ok("a TikTok lookup is refused on the free plan", r.status_code == 402)

r = c.post(f"/api/accounts/{aid}/sync?mode=full"); body = r.json()
ok("syncing a YouTube channel works on the free plan and costs no credits", r.status_code == 200 and body.get("credits_est") == 0 and body.get("source") == "youtube_api")
posts = {x["external_id"]: x for x in db.query("SELECT * FROM posts WHERE workspace_id=%s AND account_id=%s", (ws, aid))}
ok("the recent videos were stored, the 60-day-old one was not", set(posts) == {"short000001", "long0000002"})
sp = posts["short000001"]
ok("views, likes, comments and length are saved", sp["views"] == 5400 and sp["likes"] == 310 and sp["comments"] == 12 and round(sp["duration_s"]) == 45 and sp["status"] == "posted")
ok("the channel id and subscriber snapshot are saved", db.one("SELECT external_id FROM accounts WHERE id=%s", (aid,))["external_id"] == CHAN and db.one("SELECT followers FROM account_snapshots WHERE account_id=%s ORDER BY day DESC LIMIT 1", (aid,))["followers"] == 12345)
ok("no SociaVault credits were spent", (db.one("SELECT COALESCE(SUM(credits),0) AS n FROM credit_usage WHERE workspace_id=%s", (ws,))["n"]) == 0)
r = c.post(f"/api/accounts/{aid}/sync"); ok("the same channel is not re-fetched within 15 minutes", r.status_code != 200 or r.json().get("skipped") or "moment ago" in r.text)

# light mode keeps short-form only
db.execute("UPDATE accounts SET last_synced_at = now() - interval '2 hours' WHERE id=%s", (aid,))
db.execute("DELETE FROM posts WHERE account_id=%s", (aid,))
out = sociavault.sync_youtube_account_official(ws, aid, 14, mode="light")
ok("light mode keeps the short-form video only", out["upserted"] == 1 and db.one("SELECT COUNT(*) AS n FROM posts WHERE account_id=%s", (aid,))["n"] == 1)

# limits
for h in ("@c1", "@c2"):
    ok(f"channel {h} can be added up to the cap", c.post("/api/accounts", json={"handle": h, "platform": "YouTube"}).status_code == 200)
r = c.post("/api/accounts", json={"handle": "@c3", "platform": "YouTube"}); ok("a fourth channel is refused at the cap of 3", r.status_code == 402)
r = c.post("/api/billing/addons/checkout", json={"kind": "connected", "qty": 1}); ok("add-ons are refused on the free plan", r.status_code == 400 and "paid" in r.text)
try:
    billing.require_connected(ws, 0); pub = False
except Exception as e:
    pub = getattr(e, "status_code", 0) == 402
ok("connecting a publishing account is refused on the free plan", pub)
try:
    billing.check_credits(ws, 1); cred = False
except Exception as e:
    cred = getattr(e, "status_code", 0) == 402
ok("SociaVault credits are not part of the free plan", cred)
db.execute("INSERT INTO ai_usage(workspace_id, month, calls) VALUES (%s,%s,20) ON CONFLICT (workspace_id, month) DO UPDATE SET calls=20", (ws, billing._month()))
try:
    billing.check_ai(ws); ai = False
except Exception as e:
    ai = getattr(e, "status_code", 0) == 402 and "Free" in str(getattr(e, "detail", ""))
ok("AI stops at the free plan's 20 requests a month", ai)

# a TikTok account left over from an earlier plan is not synced on the free plan
tid = db.one("INSERT INTO accounts(workspace_id, handle, platform, status) VALUES (%s,'@old_tt','TikTok','active') RETURNING id", (ws,))["id"]
try:
    sociavault.sync_account(ws, tid); skipped = False
except sociavault.SociaVaultError as e:
    skipped = e.status == 402 and "YouTube" in str(e)
ok("a leftover TikTok account is not synced on the free plan", skipped)

# upgrading lifts the limits
billing.set_subscription(ws, "pro", status="comp")
ok("a paid plan lifts the YouTube-only rule", billing.workspace_state(ws)["state"] == "comp" and billing.platform_allowed(ws, "TikTok"))
r = c.post("/api/accounts", json={"handle": "@tikkie", "platform": "TikTok"}); ok("a TikTok account can be added on a paid plan", r.status_code == 200)
db.execute("DELETE FROM workspace_subscriptions WHERE workspace_id=%s", (ws,)); db.execute("DELETE FROM ai_usage WHERE workspace_id=%s", (ws,))

# the free plan can be switched off or edited
billing.save_free_plan(enabled=False)
ok("with the free plan off, no plan means read-only again", billing.workspace_state(ws)["state"] == "expired" and billing.access_blocked(ws))
billing.save_free_plan(enabled=True, max_accounts=5, max_ai_calls=-1)
ok("the caps can be edited and AI can be unlimited", billing.effective_plan(ws)["max_accounts"] == 5 and billing.effective_plan(ws)["max_ai_calls"] == -1)
billing.save_free_plan(max_accounts=3, max_ai_calls=20)
db.execute("DELETE FROM platform_settings WHERE key IN ('free_plan')")
_k = os.environ.pop("YOUTUBE_API_KEY")
ok("without the Google key there is no free plan: a plan-less workspace stays read-only as before", billing.free_plan_on() is False and billing.workspace_state(ws)["state"] == "expired" and "free_plan" not in c.get("/api/public/pricing").json())
os.environ["YOUTUBE_API_KEY"] = _k
ok("no key set means YouTube falls back to SociaVault", (lambda: (os.environ.pop("YOUTUBE_API_KEY"), (not sociavault.official_youtube()))[1])())
os.environ["YOUTUBE_API_KEY"] = KEY
billing.set_enabled(False)
print("ALL OK")
