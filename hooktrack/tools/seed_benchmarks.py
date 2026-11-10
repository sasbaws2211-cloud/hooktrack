"""Build the peer-benchmark seed set: what a typical account of each size and niche gets per post.

    python tools/seed_benchmarks.py --youtube-discover 40 --dry-run      # YouTube only, free (official API), prints the result
    python tools/seed_benchmarks.py --accounts tools/seed_accounts.json  # TikTok / Instagram / YouTube handles you listed
    python tools/seed_benchmarks.py --youtube-discover 40 --accounts tools/seed_accounts.json

It writes ONLY anonymous aggregates (quartiles of per-account medians) to the peer_benchmarks table, source 'seed'. Handles
and posts are read, summarised in memory and thrown away; nothing about a sampled account is stored.

Sampling matters more than volume. A benchmark built from viral accounts tells a beginner they are failing. So:
  * YouTube discovery samples RECENT uploads on a niche term (not a "top channels" list), which is mostly ordinary channels;
  * for TikTok and Instagram, list ordinary accounts of every size, not only well-known ones.

seed_accounts.json looks like:  {"tiktok": {"fitness": ["handle1", "handle2"]}, "instagram": {"food": ["handle3"]},
                                 "youtube": {"gaming": ["@handle4"]}}
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
    ap.add_argument("--niches", help="comma-separated niches to limit YouTube discovery to (each niche costs about 200 units)")
    ap.add_argument("--accounts", help="JSON file of handles per platform and niche")
    ap.add_argument("--dry-run", action="store_true", help="print the aggregates, write nothing")
    a = ap.parse_args()
    if not a.youtube_discover and not a.accounts:
        ap.error("give --youtube-discover and/or --accounts")

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
