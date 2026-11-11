"""The TikTok seed tool, run in small credit batches: caps, band filling, resuming, staging, and that search-result views are
never used as a benchmark. Run: python tests/seed_tool_check.py (local test database; SociaVault is faked, no network)."""
import importlib.util, os, sys, time
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location("seed_tool", os.path.join(ROOT, "tools", "seed_benchmarks.py"))
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
from app import db, peer, sociavault, vaultwatch

db.init_schema()
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
db.execute("DELETE FROM peer_seed_samples")
db.execute("DELETE FROM peer_benchmarks")
peer.forget()
NOW = int(time.time())
calls = {"search": 0, "videos": 0}
measured_handles = []
FOLLOWERS = [300, 700, 2500, 8000, 30000, 90000, 400000, 2000000]


def search_page(n):
    items = []
    for i in range(30):
        f = FOLLOWERS[(i + n) % len(FOLLOWERS)]
        items.append({"aweme_info": {"author": {"unique_id": f"acct{n % 6}_{i}", "follower_count": f},     # pages repeat every 6: duplicates
                                     "statistics": {"play_count": 9_000_000},                               # a viral search hit: never used
                                     "create_time": NOW - 5 * 86400}})
    return {"search_item_list": {str(i): it for i, it in enumerate(items)}, "cursor": 30 * (n + 1), "has_more": 1}


page_no = {"n": 0}


def fake_get(path, params, key):
    if path == "instagram/search/hashtag":
        return ig_search(params)
    assert path == "tiktok/search/keyword"
    calls["search"] += 1
    page_no["n"] += 1
    return search_page(page_no["n"])


def fake_videos(handle, key, **kw):
    calls["videos"] += 1
    measured_handles.append(handle)
    items = [{"is_top": True, "create_time": NOW - 300 * 86400, "statistics": {"play_count": 5_000_000}},        # pinned: ignored
             {"create_time": NOW - 3600, "statistics": {"play_count": 777_777}}]                               # too new: ignored
    items += [{"create_time": NOW - (4 + i) * 86400, "statistics": {"play_count": 100 + i}} for i in range(12)]
    return {"aweme_list": items}


sociavault._get = fake_get
sociavault.tiktok_videos = fake_videos
vaultwatch._fetch_credits = lambda key: 50

# ---- discovery
cr = seed.Credits(500)
needs = {b: 4 for b in seed.BAND_KEYS}
cand = seed.tiktok_discover_candidates("k", 4, cr, max_search=10, needs=needs, skip=set())
ok("every follower band gets candidates, no more than twice what it needs", all(4 <= len(cand[b]) <= 8 for b in seed.BAND_KEYS))
ok("no account is listed twice", len({h for v in cand.values() for h in v}) == sum(len(v) for v in cand.values()))
ok("discovery stops once every band is full, spending little", 1 <= cr.used <= 10 and calls["search"] == cr.used)
page_no["n"] = 0
cr2 = seed.Credits(500)
seed.tiktok_discover_candidates("k", 500, cr2, max_search=3)
ok("the search budget is a hard limit", cr2.used == 3)
page_no["n"] = 0
only_big = seed.tiktok_discover_candidates("k", 5, seed.Credits(500), max_search=8, needs={"under-1k": 0, "1k-10k": 0, "10k-100k": 0, "100k-plus": 5})
ok("bands that are already full are not searched for", not only_big["under-1k"] and not only_big["1k-10k"] and len(only_big["100k-plus"]) >= 5)
skipped = {peer._handle_hash("tiktok", h) for h in list(cand["under-1k"])[:2]}
page_no["n"] = 0
again = seed.tiktok_discover_candidates("k", 4, seed.Credits(500), max_search=10, needs=needs, skip=skipped)
ok("accounts measured in an earlier batch are skipped", not any(peer._handle_hash("tiktok", h) in skipped for v in again.values() for h in v))

# ---- measuring
calls["videos"] = 0
cr3 = seed.Credits(10)
r = seed.tiktok_measure("someone", 450, "k", cr3)
ok("measuring one account costs one credit", cr3.used == 1 and calls["videos"] == 1)
ok("pinned and brand-new posts are ignored; the median is the account's own", r["n"] == 12 and r["median"] == 105.5)
sociavault.tiktok_videos = lambda handle, key, **kw: {"aweme_list": [{"create_time": NOW - 5 * 86400, "statistics": {"play_count": 50}}] * 3}
ok("an account with too few mature posts is not used", seed.tiktok_measure("tiny", 100, "k", seed.Credits(5)) is None)
sociavault.tiktok_videos = fake_videos

# ---- batches
page_no["n"] = 0; calls.update(search=0, videos=0); measured_handles.clear()
out1 = seed.tiktok_batch("k", per_band=20, cap=45, max_search=12, reserve=0)
ok("a batch never spends more than its cap", out1["credits"] <= 45 and calls["search"] + calls["videos"] == out1["credits"])
c1 = peer.staged_counts("tiktok")
ok("a batch stages the accounts it measured, across bands", out1["measured"] == sum(c1.values()) > 0)
ok("nothing is published by a batch", db.one("SELECT COUNT(*) AS n FROM peer_benchmarks")["n"] == 0)
cols = {r["column_name"] for r in db.query("SELECT column_name FROM information_schema.columns WHERE table_name='peer_seed_samples'")}
ok("only hashes are kept, never handles", "handle" not in cols and "handle_hash" in cols)
first = list(measured_handles)

page_no["n"] = 0
out2 = seed.tiktok_batch("k", per_band=20, cap=45, max_search=12, reserve=0)
second = measured_handles[len(first):]
ok("the next batch resumes: it never measures an account twice", not set(first) & set(second))
c2 = peer.staged_counts("tiktok")
ok("and the staged total grows", sum(c2.values()) > sum(c1.values()))
for _ in range(6):
    page_no["n"] = 0
    seed.tiktok_batch("k", per_band=20, cap=45, max_search=12, reserve=0)
ok("batches stop looking once every band has its target", all(v <= 20 for v in peer.staged_counts("tiktok").values()))
before = calls["search"] + calls["videos"]
out_done = seed.tiktok_batch("k", per_band=min(peer.staged_counts("tiktok").values()), cap=45, max_search=12, reserve=0)
ok("a finished target spends no credits", out_done["credits"] == 0 and calls["search"] + calls["videos"] == before)

# ---- low balance
vaultwatch._fetch_credits = lambda key: 4
try:
    seed.tiktok_batch("k", per_band=50, cap=45, max_search=12, reserve=0); refused = False
except SystemExit as e:
    refused = "Not enough credits" in str(e)
ok("it refuses to start a batch the balance cannot cover", refused)
vaultwatch._fetch_credits = lambda key: 50

# ---- publishing is a separate, explicit step
staged = peer.staged_samples("tiktok")
ok("every staged sample is general-niche and uses the account's own median", staged and all(s["niche"] == "general" and s["median_views"] == 106 for s in staged))
ok("nothing near the viral search views got through", max(s["median_views"] for s in staged) < 1000)
sys.argv = ["seed_benchmarks.py", "--staging-report"]
ok("the report runs", seed.main() == 0)
ok("still nothing published", db.one("SELECT COUNT(*) AS n FROM peer_benchmarks")["n"] == 0)
sys.argv = ["seed_benchmarks.py", "--publish"]
ok("publish runs", seed.main() == 0)
rows = db.query("SELECT platform, source, band, n_accounts, median FROM peer_benchmarks")
ok("publishing writes TikTok seed groups only", rows and all(r["platform"] == "tiktok" and r["source"] == "seed" and r["median"] == 106 for r in rows))
ok("the live lookup now answers for a beginner", peer.lookup("TikTok", "main", "fitness", 500) is not None)

# ======================= Instagram =======================
ig = {"search": 0, "profile": 0, "reels": 0, "n": 0}
SIZES = [40, 600, 3000, 7000, 25000, 80000, 300000, 1500000]


def ig_search(params):
    ig["search"] += 1
    ig["n"] += 1
    n = ig["n"]
    posts = {str(i): {"owner": {"username": f"ig{n % 5}_{i}_{SIZES[(i + n) % 8]}"}, "like_count": SIZES[(i + n) % 8] // 20,
                      "video_play_count": None} for i in range(10)}
    return {"data": {"posts": posts, "cursor": str(n + 1)}}


def ig_profile(handle, key):
    ig["profile"] += 1
    return {"data": {"user": {"edge_followed_by": {"count": int(handle.rsplit("_", 1)[1])}}}}


def ig_reels(handle, key, max_id=None):
    ig["reels"] += 1
    measured_handles.append(handle)
    items = [{"media": {"taken_at": NOW - 3600, "play_count": 999_999}}]                                  # too new: ignored
    items += [{"media": {"taken_at": NOW - (4 + i) * 86400, "play_count": 200 + i}} for i in range(9)]
    return {"data": {"items": {str(i): it for i, it in enumerate(items)}}}


sociavault.instagram_profile = ig_profile
sociavault.instagram_reels = ig_reels
vaultwatch._fetch_credits = lambda key: 50
cr = seed.Credits(100)
cand = seed.instagram_discover_candidates("k", cr, max_search=4, want=500, skip=set())
ok("hashtag discovery finds usernames with a like count, within its search budget", len(cand) >= 10 and cr.used == 4 == ig["search"])
done = {peer._handle_hash("instagram", h) for h in list(cand)[:5]}
cand2 = seed.instagram_discover_candidates("k", seed.Credits(100), max_search=4, want=500, skip=done)
ok("accounts measured in an earlier batch are not offered again", not (set(cand2) & set(list(cand)[:5])))
cr = seed.Credits(10)
ok("a profile call costs one credit and returns the follower count", seed.instagram_followers("ig0_0_600", "k", cr) == 600 and cr.used == 1)
cr = seed.Credits(10)
r = seed.instagram_measure("ig0_0_600", 600, "k", cr)
ok("a reels call costs one credit; brand-new reels are ignored", cr.used == 1 and r["n"] == 9 and r["median"] == 204)

tt_before = peer.staged_counts("tiktok")
ig.update(search=0, profile=0, reels=0, n=0); measured_handles.clear()
out = seed.instagram_batch("k", per_band=4, cap=45, max_search=10, reserve=0)
ok("an Instagram batch never spends more than its cap", out["credits"] <= 45 and ig["search"] + ig["profile"] + ig["reels"] == out["credits"])
ic = peer.staged_counts("instagram")
ok("it stages Instagram accounts without touching TikTok's", out["measured"] == sum(ic.values()) > 0 and peer.staged_counts("tiktok") == tt_before)
ok("full bands are never paid for: reels calls equal accounts staged plus empties, not profiles looked at",
   ig["reels"] <= ig["profile"] and all(v <= 4 for v in ic.values()))
ok("the small bands, which need the most help, are filled first", ic["under-1k"] > 0)
first = list(measured_handles)
ig["n"] = 0
seed.instagram_batch("k", per_band=4, cap=45, max_search=10, reserve=0)
ok("the next batch resumes without measuring anyone twice", not set(first) & set(measured_handles[len(first):]))
for _ in range(5):
    ig["n"] = 0
    seed.instagram_batch("k", per_band=4, cap=45, max_search=10, reserve=0)
ok("over several batches every band is reached and no target is exceeded", all(v == 4 for v in peer.staged_counts("instagram").values()))
ig_staged = peer.staged_samples("instagram")
ok("staged Instagram samples are general-niche and use the account's own median", ig_staged and all(x["niche"] == "general" and x["median_views"] == 204 for x in ig_staged))
db.execute("DELETE FROM peer_benchmarks"); peer.forget()
sys.argv = ["seed_benchmarks.py", "--publish", "--platform", "instagram"]
ok("publishing Instagram works", seed.main() == 0)
rows = db.query("SELECT DISTINCT platform FROM peer_benchmarks")
ok("and writes Instagram groups only", [r["platform"] for r in rows] == ["instagram"])
db.execute("DELETE FROM peer_seed_samples"); db.execute("DELETE FROM peer_benchmarks"); peer.forget()
print("ALL OK")
