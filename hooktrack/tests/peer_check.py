"""Peer benchmarks: a beginner's posts are compared with similar accounts until the account has a normal of its own.
Run: python tests/peer_check.py (local test database; nothing leaves the machine)."""
import os, sys
from datetime import date, timedelta
os.environ.setdefault("DATABASE_URL", "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db, logic, peer, sociavault

db.init_schema()
ok = lambda n, cond: print(("PASS " if cond else "FAIL ") + n) or (cond or sys.exit(1))
db.execute("DELETE FROM peer_benchmarks")
peer.forget()

# ---- pure helpers
ok("niche words map to buckets", peer.niche_bucket("Fitness & gym tips") == "fitness" and peer.niche_bucket("home recipes") == "food"
   and peer.niche_bucket("") == "general" and peer.niche_bucket("beekeeping") == "general" and peer.niche_bucket("gaming") == "gaming")
ok("follower bands", [peer.band_for(x) for x in (None, 0, 999, 1000, 9999, 10000, 250000)] == [None, "under-1k", "under-1k", "1k-10k", "1k-10k", "10k-100k", "100k-plus"])
ok("platform names are normalised", peer.canonical_platform("YouTube") == "youtube" and peer.canonical_platform("Instagram") == "instagram" and peer.canonical_platform("TikTok") == "tiktok")
q = peer.quartiles([10, 20, 30, 40, 50])
ok("quartiles", q == (20, 30, 40))

# ---- nothing stored: no benchmark, nothing breaks
ok("with no data there is no benchmark", peer.lookup("TikTok", "main", "fitness", 500) is None)

# ---- a seed group is trusted only with enough accounts
few = [{"platform": "tiktok", "fmt": "main", "niche": "fitness", "followers": 500, "median_views": 300 + i, "n_posts": 20} for i in range(4)]
peer.store_samples("seed", few)
ok("four sampled accounts are too few to use", peer.lookup("TikTok", "main", "fitness", 500) is None)
seed = [{"platform": "tiktok", "fmt": "main", "niche": "fitness", "followers": 500, "median_views": 200 + 50 * i, "n_posts": 20} for i in range(8)]
seed += [{"platform": "tiktok", "fmt": "main", "niche": "fitness", "followers": 50000, "median_views": 20000 + 1000 * i, "n_posts": 20} for i in range(8)]
peer.store_samples("seed", seed)
hit = peer.lookup("TikTok", "main", "gym and fitness", 400)
ok("a beginner is matched to their niche and size", hit and hit["band"] == "under-1k" and hit["niche"] == "fitness" and hit["exact"] and 200 <= hit["median"] <= 600 and hit["source"] == "seed")
big = peer.lookup("TikTok", "main", "fitness", 60000)
ok("a larger account gets a larger benchmark", big and big["band"] == "10k-100k" and big["median"] > 10000)
ok("unknown size falls back to all sizes", peer.lookup("TikTok", "main", "fitness", None)["band"] == "all")
other = peer.lookup("TikTok", "main", "cooking", 400)
ok("a niche with no sample falls back to the general group", other and other["niche"] == "general" and not other["exact"])
ok("another platform has no benchmark", peer.lookup("Instagram", "main", "fitness", 400) is None)

# ---- user-derived figures need 20 accounts and then win over the seed
users = [{"platform": "tiktok", "fmt": "main", "niche": "fitness", "followers": 500, "median_views": 1000, "n_posts": 10} for _ in range(19)]
peer.store_samples("users", users)
ok("19 workspace accounts are not enough to publish a group", peer.lookup("TikTok", "main", "fitness", 400)["source"] == "seed")
peer.store_samples("users", users + [{"platform": "tiktok", "fmt": "main", "niche": "fitness", "followers": 500, "median_views": 1000, "n_posts": 10}])
ok("20 are, and they replace the seed sample", peer.lookup("TikTok", "main", "fitness", 400)["source"] == "users" and peer.lookup("TikTok", "main", "fitness", 400)["median"] == 1000)
db.execute("DELETE FROM peer_benchmarks WHERE source='users'"); peer.forget()


# ---- the judge: a new account is compared with peers, an established one with itself
def make_ws(n_old, new_views, niche="fitness", followers=500):
    ws = db.one("INSERT INTO workspaces(name) VALUES ('peer test') RETURNING id")["id"]
    acc = db.one("INSERT INTO accounts(workspace_id, handle, platform, niche) VALUES (%s,'@newbie','TikTok',%s) RETURNING id", (ws, niche))["id"]
    if followers is not None:
        db.execute("INSERT INTO account_snapshots(account_id, workspace_id, day, followers) VALUES (%s,%s,current_date,%s)", (acc, ws, followers))
    ids = []
    for i, v in enumerate(n_old):
        pid = sociavault._upsert_post(ws, acc, ext_id=f"o{ws}{i}", post_date=date.today() - timedelta(days=20 + i), date_estimated=False,
                                      hook="old", caption="", views=v, comments=0)
        ids.append(pid)
    pid = sociavault._upsert_post(ws, acc, ext_id=f"n{ws}", post_date=date.today() - timedelta(days=5), date_estimated=False,
                                  hook="new", caption="", views=new_views, comments=0)
    db.execute("UPDATE posts SET views_updated_at = now() WHERE workspace_id=%s", (ws,))
    return ws, acc, pid


ws, acc, pid = make_ws([300, 400], 1500)
pf = logic.performance(ws)[pid]
ok("a new account's post is judged against peers", pf["baseline_source"] == "peer" and pf["label"] == "WINNER" and pf["baseline"] == peer.lookup("TikTok", "main", "fitness", 500)["median"])
ok("the call is marked low confidence and says what is missing", pf["confidence"] == "low" and pf["own_mature"] == 3 and pf["own_needed"] == 3 and pf["peer"]["who"])
dec, why = logic.evidence_decision(None, logic.get_settings(ws), pf)
ok("it still produces a decision, worded as an early read against similar accounts", dec == "SCALE" and "similar" in why and "typical" in why)
ok("peer calls are not logged as the account's own track record", logic.log_decisions(ws) == 0)

ws2, acc2, pid2 = make_ws([300, 400], 30)
pf2 = logic.performance(ws2)[pid2]
d2, _ = logic.evidence_decision(None, logic.get_settings(ws2), pf2)
ok("a weak post against peers is reworked, never killed", pf2["label"] == "DUD" and pf2["recent_duds"] == 0 and d2 == "MUTATE")

ws3, acc3, pid3 = make_ws([300, 400, 350, 380, 320, 360], 1500)
pf3 = logic.performance(ws3)[pid3]
ok("an account with enough posts uses its own normal", pf3["baseline_source"] == "own" and pf3["peer"] is None and pf3["own_needed"] == 0 and pf3["baseline"] < 400)

ws4, acc4, pid4 = make_ws([300, 400], 1500, niche="fitness", followers=None)
ok("without a follower count the all-sizes group is used", logic.performance(ws4)[pid4]["peer"]["band"] == "all")

db.execute("DELETE FROM peer_benchmarks"); peer.forget()
ws5, acc5, pid5 = make_ws([300, 400], 1500)
ok("with no benchmark at all the old behaviour stays: no baseline", logic.performance(ws5)[pid5]["label"] == "NO_BASELINE")

# ---- the daily refresh from workspaces publishes nothing below 20 accounts, and never raises
ok("the user-derived refresh runs and, with few accounts, writes nothing usable", peer.refresh_from_users() >= 0 and peer.lookup("TikTok", "main", "fitness", 500) is None)
for w in (ws, ws2, ws3, ws4, ws5):
    db.execute("DELETE FROM workspaces WHERE id=%s", (w,))
db.execute("DELETE FROM peer_benchmarks"); peer.forget()
print("ALL OK")
