"""The "what's working in your niche" feed: which videos qualify as outliers, what is stored, the unit budget, the schedule and the API.
Run: python tests/discover_check.py (local test database; YouTube is faked, nothing leaves the machine)."""
import os, sys
from datetime import datetime, timedelta, timezone
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
KEY = "TESTKEYabcdefghijklmnop1234"
os.environ["YOUTUBE_API_KEY"] = KEY
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import httpx
from fastapi.testclient import TestClient
from app import auth, db, discover, youtube_api as yt
from app.main import app

db.init_schema()
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
now = datetime.now(timezone.utc)
iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
cid = lambda n: "UC" + f"{n:022d}"
vid = lambda n: f"v{n:010d}"

# ---- a fake YouTube: channels, their older shorts, and the candidate videos search returns
CHANNELS = {   # n -> (subscribers, [views of older shorts], uploads has long videos too)
    1: (800, [200] * 10),          # small, steady at 200: candidates here can be outliers
    2: (3_000_000, [5000] * 10),   # far too big to be "similar"
    3: (5000, [5000] * 10),        # steady at 5,000: a 9,000 video is only 1.8x
    4: (900, [300] * 4),           # too few other videos to know its normal
    5: (700, [20] * 10),           # normal so tiny that any ratio is noise
    6: (None, [100] * 10),         # hidden subscriber count
}
VIDEOS = {}     # video id -> detail dict


def add(n, v, days, dur="PT40S", views=0):
    VIDEOS[vid(v)] = {"id": vid(v), "ch": n, "views": views, "days": days, "dur": dur}


for n, (_, olds) in CHANNELS.items():
    for i, views in enumerate(olds):
        add(n, 100 * n + i, days=20 + i, views=views)
add(1, 9001, 3, views=2000)                 # 10x its channel's 200: qualifies
add(1, 9002, 5, views=900)                  # 4.5x but under 1,000 views: noise
add(1, 9003, 4, views=3000, dur="PT10M")    # a long video: not a short
add(1, 9004, 1, views=4000)                 # too new to judge
add(1, 9005, 6, views=700)                  # 3.5x but under 1,000: noise
add(1, 9006, 7, views=1500)                 # 7.5x: qualifies
add(1, 9007, 4, views=3000)                 # 15x but off-topic for fitness (see detail(): no fitness word)
add(1, 9010, 6, views=1200)                 # 6x: a third and fourth qualifier from the same channel,
add(1, 9011, 7, views=1300)                 # 6.5x: the feed keeps only the best two per channel
add(2, 9101, 3, views=900000)               # huge channel: excluded
add(3, 9201, 3, views=9000)                 # only 1.8x
add(4, 9301, 3, views=5000)                 # no baseline
add(5, 9401, 3, views=5000)                 # baseline too small
add(6, 9501, 3, views=5000)                 # hidden subscribers
SEARCHED = [9001, 9002, 9003, 9004, 9005, 9006, 9007, 9010, 9011, 9101, 9201, 9301, 9401, 9501]
calls = []
mode = {"fail": None}


def detail(v):
    desc = "a skincare haul" if v["id"] == vid(9007) else "my daily workout and gym routine"
    return {"id": v["id"], "snippet": {"title": f"Title for {v['id']}", "description": desc, "publishedAt": iso(now - timedelta(days=v["days"])),
                                       "channelId": cid(v["ch"]), "channelTitle": f"Channel {v['ch']}"},
            "statistics": {"viewCount": str(v["views"])}, "contentDetails": {"duration": v["dur"]}}


def handler(request):
    name = request.url.path.rsplit("/", 1)[-1]
    q = dict(request.url.params)
    calls.append(name)
    if mode["fail"] == "quota":
        return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
    if q.get("key") != KEY:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}]}})
    if name == "search":
        assert q.get("videoDuration") == "short" and q.get("publishedAfter") and q.get("type") == "video"
        return httpx.Response(200, json={"items": [{"id": {"videoId": vid(v)}, "snippet": {"channelId": cid(VIDEOS[vid(v)]["ch"])}} for v in SEARCHED]})
    if name == "videos":
        return httpx.Response(200, json={"items": [detail(VIDEOS[i]) for i in q["id"].split(",") if i in VIDEOS]})
    if name == "channels":
        items = []
        for c in q["id"].split(","):
            n = int(c[2:])
            if n in CHANNELS:
                st = {"videoCount": "50"}
                if CHANNELS[n][0] is None:
                    st["hiddenSubscriberCount"] = True
                else:
                    st["subscriberCount"] = str(CHANNELS[n][0])
                items.append({"id": c, "snippet": {"title": f"Channel {n}"}, "statistics": st,
                              "contentDetails": {"relatedPlaylists": {"uploads": "UU" + c[2:]}}})
        return httpx.Response(200, json={"items": items})
    if name == "playlistItems":
        n = int(q["playlistId"][2:])
        return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v["id"]}} for v in VIDEOS.values() if v["ch"] == n]})
    return httpx.Response(404, json={})


yt._http = lambda: httpx.Client(transport=httpx.MockTransport(handler))
db.execute("DELETE FROM youtube_quota"); db.execute("DELETE FROM discover_items")
db.execute("DELETE FROM platform_settings WHERE key = 'discover_state'")

# ---- relevance, ranking and the display cap
ok("a video must mention its niche", discover.on_topic("fitness", "Leg day", "my workout") and not discover.on_topic("fitness", "Best lipstick", "a makeup haul") and discover.on_topic("food", "Dinner in 10 minutes"))
ok("huge ratios are shown as 100x+, ordinary ones plainly", discover._mult_label(2158.2) == "100x+" and discover._mult_label(9.84) == "9.8x" and discover._mult_label(10.0) == "10x")

# ---- reading a hook
hp = discover.hook_pattern
ok("a question opener is recognised", hp("Why do I never finish workouts?") == "Opens with a question" and hp("How I lost weight") == "Opens with a question")
ok("a number is recognised", hp("5 mistakes at the gym") == "Uses a number")
ok("a how-to is recognised", hp("Stop eating this at night") == "A direct how-to or instruction")
ok("a bold claim is recognised", hp("The truth about protein") == "Makes a bold claim")
ok("anything else gets a neutral label", hp("My morning") == "Tells a short story or shows a result")

# ---- refreshing a niche
res = discover.refresh_niche("fitness")
rows = {r["video_id"]: r for r in db.query("SELECT * FROM discover_items")}
ok("an off-topic video and a channel's third and fourth qualifiers are left out", vid(9007) not in rows and vid(9010) not in rows and vid(9011) not in rows)
ok("only the real outliers from similar channels are kept", set(rows) == {vid(9001), vid(9006)})
r1 = rows[vid(9001)]
ok("each keeps the numbers that explain it", r1["views"] == 2000 and r1["channel_median"] == 200 and abs(r1["multiple"] - 10.0) < 0.01 and r1["band"] == "under-1k" and r1["niche"] == "fitness" and r1["subscribers"] == 800)
ok("a long video, a too-new one, tiny numbers, a huge channel, a flat one, no baseline and hidden subscribers are all left out", all(v not in rows for v in map(vid, (9002, 9003, 9004, 9005, 9101, 9201, 9301, 9401, 9501))))
ok("the run reports what it did and stays within its unit budget", res["outliers"] == 2 and 0 < res["units"] <= 600 and calls.count("search") == 2)
discover.refresh_niche("fitness")
ok("running again updates rather than duplicates", db.one("SELECT COUNT(*) AS n FROM discover_items")["n"] == 2)

# ---- the budget cap
db.execute("DELETE FROM discover_items"); calls.clear()
res = discover.refresh_niche("food", max_units=205)
ok("a refresh stops when it has used its unit allowance", res["units"] < 230 and db.one("SELECT COUNT(*) AS n FROM discover_items")["n"] <= 2)

# ---- the API
app.dependency_overrides[auth.current] = lambda: auth.Ctx(user_id=1, email="a@example.com", name="A", workspace_id=1, role="owner")
c = TestClient(app)
db.execute("DELETE FROM discover_items")
discover.refresh_niche("fitness")
body = c.get("/api/discover").json()
it = body["items"][0]
ok("the feed lists outliers, strongest first", [i["video_id"] for i in body["items"]] == [vid(9001), vid(9006)] and it["multiple"] == 10.0)
ok("each item links out and copies nothing", it["url"] == f"https://www.youtube.com/shorts/{vid(9001)}" and it["thumb"].startswith("https://i.ytimg.com/vi/") and "video_url" not in it)
ok("each item is explained in plain words", "10x what this channel usually gets (200 views)" in it["note"] and it["pattern"] and it["band_label"] == "under 1,000 followers")
ok("the page is told the rule and the honest caution", "3x" in body["rule"] and "similar creators" in body["caution"] and "go viral" not in body["caution"].lower())
ok("it lists every niche with its count", {n["key"] for n in body["niches"]} == set(discover.NICHE_TERMS) and [n["count"] for n in body["niches"] if n["key"] == "fitness"] == [2])
ok("a niche filter works and a bad one is ignored", len(c.get("/api/discover?niche=food").json()["items"]) == 0 and len(c.get("/api/discover?niche=nonsense").json()["items"]) == 2)
ok("a size filter works", len(c.get("/api/discover?band=under-1k").json()["items"]) == 2 and len(c.get("/api/discover?band=100k-plus").json()["items"]) == 0)
ok("a limit is respected", len(c.get("/api/discover?limit=1").json()["items"]) == 1)
# ---- starting on the viewer's own niche and size
wsx = db.one("INSERT INTO workspaces(name) VALUES ('discover test') RETURNING id")["id"]
accx = db.one("INSERT INTO accounts(workspace_id, handle, platform, niche) VALUES (%s,'@mine','YouTube','gym and fitness') RETURNING id", (wsx,))["id"]
db.execute("INSERT INTO account_snapshots(account_id, workspace_id, day, followers) VALUES (%s,%s,current_date,500)", (accx, wsx))
app.dependency_overrides[auth.current] = lambda: auth.Ctx(user_id=1, email="a@example.com", name="A", workspace_id=wsx, role="owner")
m = c.get("/api/discover?mine=1").json()
ok("a first visit starts on the niche and size that fit the viewer's accounts", m["applied"] == {"niche": "fitness", "band": "under-1k"} and len(m["items"]) == 2)
ok("an explicit choice is never overridden", c.get("/api/discover?mine=1&niche=food").json()["applied"]["niche"] == "food")
db.execute("UPDATE account_snapshots SET followers = 50000 WHERE account_id = %s", (accx,))
m2 = c.get("/api/discover?mine=1").json()
ok("if nothing exists for their exact size it falls back to their niche alone", m2["applied"] == {"niche": "fitness", "band": ""} and len(m2["items"]) == 2)
db.execute("DELETE FROM accounts WHERE workspace_id = %s", (wsx,))
ok("a workspace with no accounts gets the unfiltered feed", c.get("/api/discover?mine=1").json()["applied"] == {"niche": "", "band": ""})
db.execute("DELETE FROM workspaces WHERE id = %s", (wsx,))
app.dependency_overrides.clear()
ok("it needs a signed-in user", TestClient(app).get("/api/discover").status_code == 401)

# ---- old rows are deleted
db.execute("UPDATE discover_items SET collected_at = now() - interval '20 days' WHERE video_id = %s", (vid(9006),))
discover.purge()
ok("rows past the retention window are deleted", {r["video_id"] for r in db.query("SELECT video_id FROM discover_items")} == {vid(9001)})

# ---- the schedule
db.execute("DELETE FROM discover_items"); db.execute("DELETE FROM platform_settings WHERE key = 'discover_state'"); db.execute("DELETE FROM youtube_quota")
first = discover.refresh_due()
ok("a due niche is refreshed by the scheduler", first is not None and first["niche"] in discover.NICHE_TERMS)
ok("the next call waits out the gap between runs", discover.refresh_due() is None)
state = yt.platform_get(discover.STATE_KEY)
state["_last_run"] = (now - timedelta(hours=2)).isoformat(); yt.platform_set(discover.STATE_KEY, state)
second = discover.refresh_due()
ok("then it moves on to a different niche", second is not None and second["niche"] != first["niche"])
db.execute("DELETE FROM youtube_quota"); db.execute("INSERT INTO youtube_quota(day, units) VALUES (%s,%s)", (yt._day(), yt.budget() - 1000))
state = yt.platform_get(discover.STATE_KEY); state["_last_run"] = (now - timedelta(hours=2)).isoformat(); yt.platform_set(discover.STATE_KEY, state)
ok("it never runs when the free daily allowance is nearly gone (users' syncs come first)", discover.refresh_due() is None)
db.execute("DELETE FROM youtube_quota")
state = yt.platform_get(discover.STATE_KEY)
state["_last_run"] = (now - timedelta(hours=2)).isoformat(); yt.platform_set(discover.STATE_KEY, state)
mode["fail"] = "quota"
ok("a Google quota error is absorbed, not raised", discover.refresh_due() is None)
mode["fail"] = None
os.environ.pop("YOUTUBE_API_KEY"); yt._http = yt._http
ok("with no YouTube key nothing is attempted", discover.refresh_due() is None)
os.environ["YOUTUBE_API_KEY"] = KEY

db.execute("DELETE FROM discover_items"); db.execute("DELETE FROM youtube_quota"); db.execute("DELETE FROM platform_settings WHERE key = 'discover_state'")
print("ALL OK")
