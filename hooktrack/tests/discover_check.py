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

# ======================= TikTok =======================
from app import sociavault, vaultwatch
import time as _t
TNOW = int(_t.time())
day = 86400
tt_calls = {"search": 0, "videos": 0}


def tv(author, vid_, views, days, desc="my workout today", followers=800, nick=None):
    return {"aweme_info": {"aweme_id": vid_, "desc": desc, "create_time": TNOW - days * day, "statistics": {"play_count": views},
                           "author": {"unique_id": author, "nickname": nick or author.upper(), "follower_count": followers}}}


TT_SEARCH = [tv("a1", "t1", 12000, 4), tv("a1", "t2", 4000, 5), tv("a1", "t3", 3000, 6),
             tv("a2", "t4", 900000, 4, followers=5_000_000), tv("a3", "t5", 800, 4, followers=2000),
             tv("a4", "t6", 9000, 4, desc="makeup haul", followers=3000), tv("a5", "t7", 5000, 1, followers=1500),
             tv("a6", "t8", 20000, 4, followers=900), tv("a7", "t9", 9000, 4, followers=500)]


def tt_get(path, params, key):
    assert path == "tiktok/search/keyword"
    assert params["sort_by"] == "most-liked" and params["date_posted"] == "this-week"       # never newest-first: those are all too young
    tt_calls["search"] += 1
    return {"search_item_list": {str(i): x for i, x in enumerate(TT_SEARCH)}, "cursor": 30, "has_more": 0}


def tt_videos(handle, key, **kw):
    tt_calls["videos"] += 1
    mine = [x["aweme_info"] for x in TT_SEARCH if x["aweme_info"]["author"]["unique_id"] == handle]
    base = {"a1": (10, 300), "a6": (10, 8000), "a7": (4, 300)}.get(handle, (10, 300))
    items = [{"aweme_id": f"old{handle}{i}", "statistics": {"play_count": base[1]}, "create_time": TNOW - (20 + i) * day} for i in range(base[0])]
    items.append({"aweme_id": f"pin{handle}", "is_top": True, "statistics": {"play_count": 9_999_999}, "create_time": TNOW - 400 * day})
    items += [{"aweme_id": m["aweme_id"], "statistics": m["statistics"], "create_time": m["create_time"]} for m in mine]
    return {"aweme_list": items}


sociavault._get = tt_get
sociavault.tiktok_videos = tt_videos
db.execute("DELETE FROM discover_items")
res = discover.refresh_niche_tiktok("fitness", "k")
rows = {r["video_id"]: r for r in db.query("SELECT * FROM discover_items WHERE platform='tiktok'")}
ok("TikTok keeps only real outliers: the two best from the account whose own normal they beat", set(rows) == {"t1", "t2"})
ok("a huge account, tiny numbers, an off-topic video, a too-new one, a flat account and one with no baseline are left out",
   all(v not in rows for v in ("t3", "t4", "t5", "t6", "t7", "t8", "t9")))
r = rows["t1"]
ok("the stored numbers are right and the pinned post never counts toward the normal", r["views"] == 12000 and r["channel_median"] == 300 and abs(r["multiple"] - 40.0) < 0.01 and r["band"] == "under-1k" and r["subscribers"] == 800)
ok("the run reports what it spent: one search plus one credit per account measured", res["credits"] == 4 == tt_calls["search"] + tt_calls["videos"] and res["accounts_measured"] == 3 and res["outliers"] == 2)
tt_calls.update(search=0, videos=0)
db.execute("DELETE FROM discover_items")
capped = discover.refresh_niche_tiktok("fitness", "k", max_credits=2)
ok("the credit cap is a hard limit and stops quietly", capped["credits"] == 2 and tt_calls["search"] + tt_calls["videos"] == 2 and capped["accounts_measured"] == 1)

app.dependency_overrides[auth.current] = lambda: auth.Ctx(user_id=1, email="a@example.com", name="A", workspace_id=1, role="owner")
db.execute("DELETE FROM discover_items")
discover.refresh_niche_tiktok("fitness", "k")
db.execute("DELETE FROM youtube_quota")
discover.refresh_niche("fitness")
body = c.get("/api/discover").json()
tk = [i for i in body["items"] if i["platform"] == "tiktok"]
ok("a TikTok item links to the video on TikTok and keeps no thumbnail", tk and tk[0]["url"].startswith("https://www.tiktok.com/@a1/video/") and tk[0]["thumb"] == "")
ok("a YouTube item still has its thumbnail", any(i["platform"] == "youtube" and i["thumb"].startswith("https://i.ytimg.com") for i in body["items"]))
ok("the page is offered a platform filter once TikTok items exist", [p["key"] for p in body["platforms"]] == ["youtube", "tiktok"])
ok("the platform filter works", {i["platform"] for i in c.get("/api/discover?platform=tiktok").json()["items"]} == {"tiktok"}
   and {i["platform"] for i in c.get("/api/discover?platform=youtube").json()["items"]} == {"youtube"})
app.dependency_overrides.clear()

# ---- the scheduler never spends credits on its own unless asked, and only with a healthy balance
db.execute("DELETE FROM platform_settings WHERE key IN ('discover_tiktok','discover_tiktok_state')")
sociavault.resolve_server_key = lambda: "k"
vaultwatch.scheduled_paused = lambda: False
vaultwatch.state = lambda: {"credits": 5000}
ok("TikTok discovery is off by default", discover.tiktok_enabled() is False and discover.refresh_due_tiktok() is None)
discover.set_tiktok_enabled(True)
vaultwatch.state = lambda: {"credits": 120}
ok("when switched on it still waits for a comfortable balance", discover.refresh_due_tiktok() is None)
vaultwatch.state = lambda: {"credits": 5000}
vaultwatch.scheduled_paused = lambda: True
ok("and does nothing while account syncs are paused for lack of credits", discover.refresh_due_tiktok() is None)
vaultwatch.scheduled_paused = lambda: False
tt_calls.update(search=0, videos=0)
first = discover.refresh_due_tiktok()
ok("with a healthy balance it refreshes one niche within its credit cap", first and first["platform"] == "tiktok" and first["credits"] <= discover.TT_MAX_CREDITS)
ok("the next call waits out the gap", discover.refresh_due_tiktok() is None)
discover.set_tiktok_enabled(False)
db.execute("DELETE FROM platform_settings WHERE key IN ('discover_tiktok','discover_tiktok_state')")

# ======================= Instagram =======================
ig_calls = {"search": 0, "reels": 0, "profile": 0}
IG_FOLLOWERS = {"u1": 900, "u2": 3000, "u3": 700, "u4": 5_000_000, "u5": 1200}


def ig_get(path, params, key):
    assert path == "instagram/search/hashtag" and params["media_type"] == "reels"
    ig_calls["search"] += 1
    posts = {str(i): {"shortcode": f"s{i}", "owner": {"username": f"u{1 + i % 5}"}, "like_count": 900 - i * 10,
                      "taken_at": "2026-11-09T10:00:00.000Z"} for i in range(10)}
    return {"data": {"posts": posts, "cursor": None}}


def reel(code, views, days, text="my workout routine"):
    return {"media": {"code": code, "taken_at": TNOW - days * day, "play_count": views, "caption": {"text": text}}}


def ig_reels(handle, key, max_id=None):
    ig_calls["reels"] += 1
    olds = {"u1": (10, 300), "u2": (10, 5000), "u3": (3, 300), "u4": (10, 300), "u5": (10, 300)}[handle]
    items = [reel(f"old{handle}{i}", olds[1], 20 + i) for i in range(olds[0])]
    if handle in ("u1", "u4"):
        items += [reel(f"{handle}a", 6000, 4), reel(f"{handle}b", 2000, 5, "workout tip"), reel(f"{handle}c", 1500, 6),
                  reel(f"{handle}new", 9000, 1), reel(f"{handle}off", 4000, 4, "pasta night")]
    if handle == "u2":
        items.append(reel("u2a", 7000, 4))
    if handle == "u3":
        items.append(reel("u3a", 8000, 4))
    return {"data": {"items": {str(i): it for i, it in enumerate(items)}}}


def ig_profile(handle, key):
    ig_calls["profile"] += 1
    return {"data": {"user": {"edge_followed_by": {"count": IG_FOLLOWERS[handle]}}}}


sociavault._get = ig_get
sociavault.instagram_reels = ig_reels
sociavault.instagram_profile = ig_profile
db.execute("DELETE FROM discover_items")
res = discover.refresh_niche_instagram("fitness", "k")
rows = {r["video_id"]: r for r in db.query("SELECT * FROM discover_items WHERE platform='instagram'")}
ok("Instagram keeps the two best reels from the account whose own normal they beat", set(rows) == {"u1a", "u1b"})
ok("a flat account, one with too little history, a huge account, a too-new reel and an off-topic one are left out",
   all(v not in rows for v in ("u1c", "u1new", "u1off", "u2a", "u3a", "u4a", "u4b")))
r = rows["u1a"]
ok("the stored numbers are right and the follower count places it in a band", r["views"] == 6000 and r["channel_median"] == 300 and abs(r["multiple"] - 20.0) < 0.01 and r["band"] == "under-1k" and r["subscribers"] == 900 and r["channel_id"] == "u1")
ok("it spends one search, one credit per account measured, and a profile only for accounts that had a hit",
   ig_calls == {"search": 1, "reels": 5, "profile": 2} and res["credits"] == 8 and res["accounts_measured"] == 5 and res["outliers"] == 2)
ig_calls.update(search=0, reels=0, profile=0)
db.execute("DELETE FROM discover_items")
capped = discover.refresh_niche_instagram("fitness", "k", max_credits=3)
ok("the credit cap is a hard limit and stops quietly", capped["credits"] == 3 and sum(ig_calls.values()) == 3)

app.dependency_overrides[auth.current] = lambda: auth.Ctx(user_id=1, email="a@example.com", name="A", workspace_id=1, role="owner")
db.execute("DELETE FROM discover_items")
discover.refresh_niche_instagram("fitness", "k")
body = c.get("/api/discover").json()
ig_items = [i for i in body["items"] if i["platform"] == "instagram"]
ok("an Instagram item links to the reel and keeps no thumbnail", ig_items and ig_items[0]["url"] == "https://www.instagram.com/reel/u1a/" and ig_items[0]["thumb"] == "")
ok("the platform filter offers Instagram once it has items", [p["key"] for p in body["platforms"]] == ["youtube", "instagram"] or "instagram" in [p["key"] for p in body["platforms"]])
ok("and filters to it", {i["platform"] for i in c.get("/api/discover?platform=instagram").json()["items"]} == {"instagram"})
app.dependency_overrides.clear()

db.execute("DELETE FROM platform_settings WHERE key IN ('discover_instagram','discover_instagram_state')")
sociavault.resolve_server_key = lambda: "k"
vaultwatch.scheduled_paused = lambda: False
vaultwatch.state = lambda: {"credits": 5000}
ok("Instagram discovery is off by default and the scheduler does nothing", discover.paid_enabled("instagram") is False and discover.refresh_due_instagram() is None)
discover.set_paid_enabled("instagram", True)
vaultwatch.state = lambda: {"credits": 100}
ok("when switched on it still waits for a comfortable balance", discover.refresh_due_instagram() is None)
vaultwatch.state = lambda: {"credits": 5000}
first = discover.refresh_due_instagram()
ok("with a healthy balance it refreshes one niche within its credit cap", first and first["platform"] == "instagram" and first["credits"] <= discover.TT_MAX_CREDITS)
ok("the next call waits out the gap", discover.refresh_due_instagram() is None)
ok("TikTok's switch is separate", discover.paid_enabled("tiktok") is False)
discover.set_paid_enabled("instagram", False)
db.execute("DELETE FROM platform_settings WHERE key IN ('discover_instagram','discover_instagram_state')")

db.execute("DELETE FROM discover_items"); db.execute("DELETE FROM youtube_quota"); db.execute("DELETE FROM platform_settings WHERE key = 'discover_state'")
print("ALL OK")
