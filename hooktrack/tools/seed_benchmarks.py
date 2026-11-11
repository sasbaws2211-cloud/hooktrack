"""Build the peer-benchmark seed set: what a typical account of each size and niche gets per post.

    python tools/seed_benchmarks.py --youtube-discover 40 --dry-run      # YouTube only, free (official API), prints the result
    python tools/seed_benchmarks.py --accounts tools/seed_accounts.json  # TikTok / Instagram / YouTube handles you listed
    python tools/seed_benchmarks.py --tiktok-batch                       # TikTok: measure accounts with up to 45 credits, stage them
    python tools/seed_benchmarks.py --instagram-batch                    # Instagram: same, about 3 credits per account (see below)
    python tools/seed_benchmarks.py --staging-report [--platform instagram]   # what has been staged so far, per follower band
    python tools/seed_benchmarks.py --publish [--platform instagram]          # turn the staged accounts into the live benchmark
    python tools/seed_benchmarks.py --youtube-discover 40 --accounts tools/seed_accounts.json

It writes ONLY anonymous aggregates (quartiles of per-account medians) to the peer_benchmarks table, source 'seed'. Handles
and posts are read, summarised in memory and thrown away; nothing about a sampled account is stored.

Sampling matters more than volume. A benchmark built from viral accounts tells a beginner they are failing. So:
  * YouTube discovery samples RECENT uploads on a niche term (not a "top channels" list), which is mostly ordinary channels;
  * for TikTok and Instagram, list ordinary accounts of every size, not only well-known ones.

seed_accounts.json looks like:  {"tiktok": {"fitness": ["handle1", "handle2"]}, "instagram": {"food": ["handle3"]},
                                 "youtube": {"gaming": ["@handle4"]}}
TikTok discovery finds accounts through keyword search (one credit returns ~30 authors with their follower counts) and then
measures each account from its own last ~20 posts (one credit each: the follower count comes from the search result). It
never uses the views shown in search results: search surfaces videos that are already doing well, which inflated an earlier
attempt about 50x. Accounts are filled band by band so under-1k accounts, which search rarely returns, are not starved.
Everything found is stored as niche 'general', because a search term does not make an account belong to a niche.
SociaVault credits arrive in small packs, so TikTok is seeded in BATCHES. Each --tiktok-batch spends at most --max-credits
(default 45), saves every account it measures at once (by a hash of the handle, never the handle itself) in
peer_seed_samples, skips accounts already measured, and only looks for the follower bands that still need accounts.
Nothing goes live until you look at --staging-report and run --publish.

Instagram works the same way but costs more per account. Hashtag search (1 credit, 10 reels) gives usernames and likes but
no follower count or views, so each candidate needs a profile call (1 credit, which also tells us its follower band) and,
only if that band still needs accounts, a reels call (1 credit). Candidates with few likes are tried first while the small
bands are short, because likes are the only size hint search gives. Expect roughly 3 credits per staged account.
Cost: YouTube discovery costs 200 units per niche (two searches) plus about 3 per channel (free, 10,000 units a day). TikTok and
Instagram cost about 2 SociaVault credits per account. 100 accounts is a few hundred credits, once, refreshed monthly.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line.strip())
    if m and m.group(1) not in os.environ:
        os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")

from app import logic, peer, sociavault, youtube_api  # noqa: E402

POSTS_PER_ACCOUNT = 20
NICHE_TERMS = {          # recent-upload search terms used to discover ordinary channels per niche
    "comedy": ["comedy skit", "funny video"], "fitness": ["workout routine", "gym tips"], "beauty": ["makeup tutorial", "skincare routine"],
    "food": ["easy recipe", "cooking at home"], "tech": ["business tips", "tech review"], "education": ["how to learn", "tutorial"],
    "gaming": ["gameplay", "gaming highlights"], "lifestyle": ["day in my life", "travel vlog"], "music": ["cover song", "dance"],
}


def _median_views(views: list) -> float | None:
    return logic._median(views) if len(views) >= peer.MIN_POSTS_PER_ACCOUNT else None


def youtube_account(channel_id: str = "", handle: str = "") -> dict | None:
    ch = youtube_api.channel(handle=handle, channel_id=channel_id)
    if not ch["uploads_playlist"] or ch["subscribers"] is None:
        return None
    vids = youtube_api.videos(youtube_api.recent_video_ids(ch["uploads_playlist"], 50))
    cutoff = datetime.now(timezone.utc) - timedelta(days=logic.MATURE_DAYS)
    out: dict = {"short": [], "main": []}
    for v in vids:
        try:
            when = datetime.fromisoformat(v["published"].replace("Z", "+00:00"))
        except ValueError:
            continue
        if when > cutoff:
            continue
        fmt = "short" if v["duration_s"] is not None and v["duration_s"] <= logic.SHORTS_MAX_SECONDS else "main"
        if len(out[fmt]) < POSTS_PER_ACCOUNT:
            out[fmt].append(v["views"])
    return {"followers": ch["subscribers"], "posts": out}


def discover_youtube(per_niche: int, only: list | None = None) -> dict:
    """niche -> [channel ids] sampled from recent uploads on each niche's search terms."""
    since = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    found: dict = {}
    for niche, terms in NICHE_TERMS.items():
        if only and niche not in only:
            continue
        ids: list = []
        for term in terms:
            youtube_api._spend(99)          # search.list costs 100 units; _get counts the other 1
            data = youtube_api._get("search", {"part": "snippet", "type": "video", "q": term, "order": "date",
                                               "publishedAfter": since, "maxResults": 50})
            for it in data.get("items") or []:
                cid = (it.get("snippet") or {}).get("channelId")
                if cid and cid not in ids:
                    ids.append(cid)
        found[niche] = ids[:per_niche]
        print(f"  youtube/{niche}: {len(found[niche])} channels found")
    return found


class CreditCap(Exception):
    pass


class Credits:
    """Counts SociaVault credits spent by this run and refuses to pass the cap."""

    def __init__(self, cap: int):
        self.cap, self.used = cap, 0

    def spend(self, n: int = 1) -> None:
        if self.used + n > self.cap:
            raise CreditCap(f"credit cap of {self.cap} reached")
        self.used += n


BAND_KEYS = [b[0] for b in peer.BANDS]


def tiktok_discover_candidates(key: str, per_band: int, credits: Credits, max_search: int, needs: dict | None = None,
                               skip: set | None = None) -> dict:
    """band -> {handle: followers}. Keyword search (1 credit per ~30 results), several terms, newest first. Stops when every
    band has 2x the accounts needed (some will fail to measure), or after max_search credits of searching."""
    found: dict = {b: {} for b in BAND_KEYS}
    skip = skip or set()
    needs = needs or {b: per_band for b in BAND_KEYS}
    want = {b: needs[b] * 2 for b in BAND_KEYS}
    spent = 0
    terms = [t for ts in NICHE_TERMS.values() for t in ts]
    for term in terms:
        cursor = None
        for _ in range(2):
            if spent >= max_search or all(len(found[b]) >= want[b] for b in BAND_KEYS):
                return found
            credits.spend()
            spent += 1
            params = {"query": term, "date_posted": "this-week", "sort_by": "date-posted"}
            if cursor:
                params["cursor"] = cursor
            data = sociavault._payload(sociavault._get("tiktok/search/keyword", params, key))
            items = data.get("search_item_list") or []
            items = list(items.values()) if isinstance(items, dict) else items
            for it in items:
                au = ((it or {}).get("aweme_info") or {}).get("author") or {}
                handle, f = au.get("unique_id"), au.get("follower_count")
                band = peer.band_for(int(f)) if f is not None else None
                if (handle and band and needs.get(band, 0) > 0 and len(found[band]) < want[band]
                        and peer._handle_hash("tiktok", handle) not in skip and not any(handle in v for v in found.values())):
                    found[band][handle] = int(f)
            cursor = data.get("cursor")
            if not data.get("has_more") or not cursor:
                break
    return found


def tiktok_measure(handle: str, followers: int, key: str, credits: Credits) -> dict | None:
    """One credit: the account's last posts. Uses only mature, non-pinned posts; needs MIN_POSTS_PER_ACCOUNT of them."""
    credits.spend()
    page = sociavault.tiktok_videos(handle, key)
    cutoff = datetime.now(timezone.utc).timestamp() - logic.MATURE_DAYS * 86400
    views = []
    for item in sociavault._aweme_items(page):
        if item.get("is_top"):
            continue
        ts = int(item.get("create_time") or item.get("createTime") or 0)
        if ts and ts > cutoff:
            continue
        st = item.get("statistics") or item.get("stats") or {}
        views.append(int(st.get("play_count") or st.get("playCount") or 0))
    views = views[:POSTS_PER_ACCOUNT]
    m = _median_views(views)
    return {"followers": followers, "median": m, "n": len(views)} if m else None


def tiktok_batch(key: str, per_band: int, cap: int, max_search: int, reserve: int) -> dict:
    """Measure TikTok accounts for the bands that still need them, staging each one as soon as it is measured."""
    from app import vaultwatch
    have = peer.staged_counts("tiktok")
    needs = {b: max(0, per_band - have[b]) for b in BAND_KEYS}
    print("staged so far: " + ", ".join(f"{b}={have[b]}" for b in BAND_KEYS) + f"  (target {per_band} per band)")
    if not any(needs.values()):
        print("every band already has its target; run --staging-report, then --publish")
        return {"measured": 0, "credits": 0}
    balance = vaultwatch._fetch_credits(key)
    cap = min(cap, balance - reserve)
    print(f"SociaVault balance {balance}; this batch may spend up to {max(cap, 0)}")
    if cap < 6:
        raise SystemExit(f"Not enough credits for a batch ({balance} in the account, {reserve} kept back).")
    credits = Credits(cap)
    done = peer.staged_hashes("tiktok")
    measured = 0
    try:
        found = tiktok_discover_candidates(key, max(needs.values()), credits, max_search=min(max_search, max(3, cap // 4)),
                                           needs=needs, skip=done)
        print("new candidates: " + ", ".join(f"{b}={len(v)}" for b, v in found.items()))
        for band in sorted(BAND_KEYS, key=lambda x: len(found[x])):
            for handle, followers in found[band].items():
                if needs[band] <= 0:
                    break
                try:
                    r = tiktok_measure(handle, followers, key, credits)
                except CreditCap:
                    raise
                except Exception as e:
                    print(f"  skipped one account ({type(e).__name__})")
                    continue
                if not r:
                    continue
                if peer.stage_sample("tiktok", handle, "main", "general", followers, r["median"], r["n"]):
                    measured += 1
                    needs[band] -= 1
                    print(f"  {band:10s} {followers:>9,} followers  median {r['median']:>9,.0f} over {r['n']} posts")
    except CreditCap as e:
        print(f"stopped: {e}")
    now = peer.staged_counts("tiktok")
    print(f"batch done: {measured} accounts staged, {credits.used} credits used. Staged: "
          + ", ".join(f"{b}={now[b]}" for b in BAND_KEYS))
    return {"measured": measured, "credits": credits.used}


def staging_report(platform: str = "tiktok") -> None:
    samples = peer.staged_samples(platform)
    print(f"{len(samples)} {platform} accounts staged")
    for band in BAND_KEYS + ["all"]:
        vals = [x["median_views"] for x in samples if band == "all" or peer.band_for(x["followers"]) == band]
        if vals:
            p25, med, p75 = peer.quartiles(vals)
            print(f"  {band:10s} n={len(vals):3d}  p25={p25:>9,.0f}  median={med:>9,.0f}  p75={p75:>9,.0f}")
    print(f"A group is used once it has {peer.MIN_SEED}+ accounts. Check the numbers look like ordinary accounts, then --publish.")


IG_HASHTAGS = ["fitness", "workout", "makeup", "skincare", "recipes", "foodie", "comedy", "funny", "travel", "lifestyle",
               "gaming", "music", "dance", "business", "education", "fashion", "ootd", "diy", "pets", "motivation"]


def instagram_discover_candidates(key: str, credits: Credits, max_search: int, want: int, skip: set) -> dict:
    """handle -> the most likes seen on one of its recent reels (a rough size hint). Hashtag search: 1 credit = 10 reels."""
    found: dict = {}
    spent = 0
    for tag in IG_HASHTAGS:
        cursor = None
        for _ in range(2):
            if spent >= max_search or len(found) >= want:
                return found
            credits.spend()
            spent += 1
            params = {"hashtag": tag, "media_type": "reels", "date_posted": "last-week"}
            if cursor:
                params["cursor"] = cursor
            data = sociavault._payload(sociavault._get("instagram/search/hashtag", params, key))
            posts = data.get("posts") or {}
            posts = list(posts.values()) if isinstance(posts, dict) else posts
            for it in posts:
                u = ((it or {}).get("owner") or {}).get("username")
                if u and peer._handle_hash("instagram", u) not in skip:
                    found[u] = max(found.get(u, 0), int((it or {}).get("like_count") or 0))
            cursor = data.get("cursor")
            if not posts or not cursor:
                break
    return found


def instagram_followers(handle: str, key: str, credits: Credits) -> int | None:
    credits.spend()
    return sociavault._profile_stats("instagram", sociavault.instagram_profile(handle, key))["followers"]


def instagram_measure(handle: str, followers: int, key: str, credits: Credits) -> dict | None:
    """One credit: the account's recent reels. Mature reels only; needs MIN_POSTS_PER_ACCOUNT of them."""
    credits.spend()
    data = sociavault._payload(sociavault.instagram_reels(handle, key))
    items = data.get("items") or data.get("reels") or data.get("posts") or data.get("data") or []
    items = list(items.values()) if isinstance(items, dict) else items
    cutoff = datetime.now(timezone.utc).timestamp() - logic.MATURE_DAYS * 86400
    views = []
    for item in items if isinstance(items, list) else []:
        media = (item or {}).get("media") or item
        ts = int(media.get("taken_at") or media.get("taken_at_timestamp") or media.get("timestamp") or 0)
        if ts and ts > cutoff:
            continue
        v = media.get("video_play_count") or media.get("play_count") or media.get("view_count") or media.get("ig_play_count")
        if v:
            views.append(int(v))
    views = views[:POSTS_PER_ACCOUNT]
    m = _median_views(views)
    return {"followers": followers, "median": m, "n": len(views)} if m else None


def instagram_batch(key: str, per_band: int, cap: int, max_search: int, reserve: int) -> dict:
    from app import vaultwatch
    have = peer.staged_counts("instagram")
    needs = {b: max(0, per_band - have[b]) for b in BAND_KEYS}
    print("staged so far: " + ", ".join(f"{b}={have[b]}" for b in BAND_KEYS) + f"  (target {per_band} per band)")
    if not any(needs.values()):
        print("every band already has its target; run --staging-report --platform instagram, then --publish --platform instagram")
        return {"measured": 0, "credits": 0}
    balance = vaultwatch._fetch_credits(key)
    cap = min(cap, balance - reserve)
    print(f"SociaVault balance {balance}; this batch may spend up to {max(cap, 0)}")
    if cap < 8:
        raise SystemExit(f"Not enough credits for a batch ({balance} in the account, {reserve} kept back).")
    credits = Credits(cap)
    done = peer.staged_hashes("instagram")
    measured = looked = 0
    try:
        cand = instagram_discover_candidates(key, credits, min(max_search, max(2, cap // 5)), want=60, skip=done)
        print(f"new candidates: {len(cand)}")
        small = lambda: needs["under-1k"] > 0 or needs["1k-10k"] > 0
        pool = dict(cand)
        while pool and any(needs.values()):
            handle = (min if small() else max)(pool, key=pool.get)            # few likes first while the small bands are short
            pool.pop(handle)
            try:
                followers = instagram_followers(handle, key, credits)
            except CreditCap:
                raise
            except Exception as e:
                print(f"  skipped one account ({type(e).__name__})")
                continue
            looked += 1
            band = peer.band_for(followers)
            if not band or needs[band] <= 0:
                continue                                                     # that band is full: do not pay for its reels
            try:
                r = instagram_measure(handle, followers, key, credits)
            except CreditCap:
                raise
            except Exception as e:
                print(f"  skipped one account ({type(e).__name__})")
                continue
            if r and peer.stage_sample("instagram", handle, "main", "general", followers, r["median"], r["n"]):
                measured += 1
                needs[band] -= 1
                print(f"  {band:10s} {followers:>9,} followers  median {r['median']:>9,.0f} over {r['n']} reels")
    except CreditCap as e:
        print(f"stopped: {e}")
    now = peer.staged_counts("instagram")
    print(f"batch done: {measured} accounts staged from {looked} looked at, {credits.used} credits used. Staged: "
          + ", ".join(f"{b}={now[b]}" for b in BAND_KEYS))
    return {"measured": measured, "credits": credits.used}


def tiktok_account(handle: str, key: str) -> dict | None:
    prof = sociavault.tiktok_profile(handle, key)
    followers = sociavault._profile_stats("tiktok", prof)["followers"]
    page = sociavault.tiktok_videos(handle, key)
    cutoff = datetime.now(timezone.utc).timestamp() - logic.MATURE_DAYS * 86400
    views = []
    for item in sociavault._aweme_items(page):
        if item.get("is_top"):
            continue                       # pinned posts are old and unrepresentative
        ts = int(item.get("create_time") or item.get("createTime") or 0)
        if ts and ts > cutoff:
            continue
        st = item.get("statistics") or item.get("stats") or {}
        views.append(int(st.get("play_count") or st.get("playCount") or 0))
    return {"followers": followers, "posts": {"main": views[:POSTS_PER_ACCOUNT]}}


def instagram_account(handle: str, key: str) -> dict | None:
    prof = sociavault.instagram_profile(handle, key)
    followers = sociavault._profile_stats("instagram", prof)["followers"]
    data = sociavault._payload(sociavault.instagram_reels(handle, key))
    items = data.get("items") or data.get("reels") or data.get("posts") or data.get("data") or []
    items = list(items.values()) if isinstance(items, dict) else items
    cutoff = datetime.now(timezone.utc).timestamp() - logic.MATURE_DAYS * 86400
    views = []
    for item in items if isinstance(items, list) else []:
        media = item.get("media") or item
        ts = int(media.get("taken_at") or media.get("taken_at_timestamp") or media.get("timestamp") or 0)
        if ts and ts > cutoff:
            continue
        v = media.get("video_play_count") or media.get("play_count") or media.get("view_count") or media.get("ig_play_count")
        if v:
            views.append(int(v))
    return {"followers": followers, "posts": {"main": views[:POSTS_PER_ACCOUNT]}}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--youtube-discover", type=int, metavar="N", help="sample up to N channels per niche from recent YouTube uploads")
    ap.add_argument("--tiktok-batch", action="store_true", help="TikTok: measure accounts for the bands that still need them and stage them")
    ap.add_argument("--per-band", type=int, default=20, help="target number of staged TikTok accounts per follower band (default 20)")
    ap.add_argument("--max-credits", type=int, default=45, help="hard cap on SociaVault credits one batch may spend (default 45)")
    ap.add_argument("--max-search", type=int, default=12, help="most credits one batch spends on discovery searches (default 12)")
    ap.add_argument("--reserve", type=int, default=0, help="credits to leave in the account (default 0)")
    ap.add_argument("--instagram-batch", action="store_true", help="Instagram: measure accounts for the bands that still need them and stage them")
    ap.add_argument("--platform", default="tiktok", choices=["tiktok", "instagram"], help="platform for --staging-report and --publish (default tiktok)")
    ap.add_argument("--staging-report", action="store_true", help="print the staged accounts per follower band")
    ap.add_argument("--publish", action="store_true", help="turn the staged accounts into the live benchmark")
    ap.add_argument("--niches", help="comma-separated niches to limit YouTube discovery to (each niche costs about 200 units)")
    ap.add_argument("--accounts", help="JSON file of handles per platform and niche")
    ap.add_argument("--dry-run", action="store_true", help="print the aggregates, write nothing")
    a = ap.parse_args()
    if a.staging_report:
        staging_report(a.platform)
        return 0
    if a.publish:
        print(f"published {peer.publish_staged(a.platform)} {a.platform} benchmark groups (source 'seed')")
        return 0
    if a.tiktok_batch:
        tiktok_batch(sociavault.resolve_server_key(), a.per_band, a.max_credits, a.max_search, a.reserve)
        return 0
    if a.instagram_batch:
        instagram_batch(sociavault.resolve_server_key(), a.per_band, a.max_credits, a.max_search, a.reserve)
        return 0
    if not a.youtube_discover and not a.accounts:
        ap.error("give --youtube-discover, --tiktok-batch, --staging-report, --publish and/or --accounts")

    jobs: list = []        # (platform, niche, kind, ref)
    if a.youtube_discover:
        for niche, ids in discover_youtube(a.youtube_discover, a.niches.split(',') if a.niches else None).items():
            jobs += [("youtube", niche, "id", cid) for cid in ids]
    if a.accounts:
        for plat, niches in json.loads(Path(a.accounts).read_text(encoding="utf-8")).items():
            for niche, handles in niches.items():
                jobs += [(plat, niche, "handle", h) for h in handles]

    key = sociavault.resolve_server_key()
    samples: list = []
    failed = 0
    for plat, niche, kind, ref in jobs:
        try:
            if plat == "youtube":
                got = youtube_account(channel_id=ref) if kind == "id" else youtube_account(handle=ref)
            elif plat == "tiktok":
                got = tiktok_account(ref, key)
            elif plat == "instagram":
                got = instagram_account(ref, key)
            else:
                continue
        except Exception as e:          # one bad account never stops the run
            failed += 1
            print(f"  skipped {plat}/{niche} ({type(e).__name__}: {str(e)[:80]})")
            continue
        if not got:
            continue
        for fmt, views in got["posts"].items():
            m = _median_views(views)
            if m:
                samples.append({"platform": plat, "fmt": fmt, "niche": niche, "followers": got["followers"],
                                "median_views": m, "n_posts": len(views)})
    print(f"{len(samples)} usable account samples from {len(jobs)} accounts ({failed} skipped)")

    if a.dry_run:
        groups: dict = {}
        for s in samples:
            for band in {peer.band_for(s["followers"]), "all"} - {None}:
                groups.setdefault((s["platform"], s["fmt"], s["niche"], band), []).append(s["median_views"])
        for k, v in sorted(groups.items()):
            if len(v) >= 3:
                p25, med, p75 = peer.quartiles(v)
                print(f"  {'/'.join(k):45s} n={len(v):3d}  p25={p25:,.0f}  median={med:,.0f}  p75={p75:,.0f}")
        print("dry run: nothing written")
        return 0
    wrote = peer.store_samples("seed", samples)
    print(f"wrote {wrote} benchmark groups (source 'seed'). A group is used once it has {peer.MIN_SEED}+ accounts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
