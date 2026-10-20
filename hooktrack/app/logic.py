"""Scoring and decision rules (the spreadsheet's logic, made configurable)."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import cache, db

DEFAULT_SETTINGS = {
    "scale_weekly": 100_000,   # weekly views that earn SCALE (needs a best hook)
    "kill_after_posts": 20,    # this many posts without a breakout = KILL
    "stall_views": 1_000,      # "breakout" = any single post at/above this
    "scale_vsc": 24,           # VSC /30 at or above -> SCALE
    "mutate_vsc": 21,          # VSC /30 at or above (below scale) -> MUTATE
    "winner_pct": 200,         # a post is a WINNER at this % of its own account's median views (200 = 2x)
    "credit_budget": 0,        # SociaVault credits per month to stay within (0 = no budget): the dashboard warns near it
    "weekly_target": 0,        # videos to film per week (0 = no target): the dashboard shows progress
    "hold_low_pct": 70,        # below this % of the account's normal a post is "below normal" (rework it)
    "hold_high_pct": 120,      # above this % it is "above normal" (strengthen it); in between is Hold
    "read_on_send": 0,         # 1 = read a Scale hook's video (about 1 SociaVault credit) when it is sent to Posts
}

EARLY_WINDOW_DAYS = 1           # the "early read" is the first day after posting (about 24-48 hours)
MIN_COMMENTS_FOR_SHARE = 10    # a share of fewer comments than this is noise
MATURE_DAYS = 2                # younger posts are still accumulating views: never judged
BASELINE_POSTS = 30            # an account's "normal" = median of its last 30 mature posts
MIN_BASELINE = 5               # fewer than this and there is no honest baseline
DUD_MULTIPLE = 0.5
HOLD_LOW, HOLD_HIGH = 0.7, 1.2   # about normal: nothing to change. Above it: strengthen. Below it: rework.
SETTLED_DAYS = 8                 # younger than this, a video is still being distributed: its call can move
SHORTS_MAX_SECONDS = 180       # YouTube Shorts can run up to 3 minutes
HIGH_INTENT_MULTIPLE = 1.5     # intent rate this many times the account's median counts as "high intent"


@cache.workspace_cached("settings", ttl=120)
def get_settings(ws: int) -> dict:
    out = dict(DEFAULT_SETTINGS)
    for row in db.query("SELECT key, value FROM settings WHERE workspace_id = %s", (ws,)):
        if row["key"] in out:
            out[row["key"]] = row["value"]
    return out


def save_settings(ws: int, values: dict) -> dict:
    for k, v in values.items():
        if k in DEFAULT_SETTINGS:
            db.execute(
                "INSERT INTO settings(workspace_id, key, value) VALUES (%s, %s, %s) "
                "ON CONFLICT (workspace_id, key) DO UPDATE SET value = EXCLUDED.value",
                (ws, k, db.Json(int(v))),
            )
    return get_settings(ws)


def vsc_total(v, s, c):
    if v is None or s is None or c is None:
        return None
    return v + s + c


def post_decision(total, st) -> str:
    if total is None:
        return ""
    if total >= st["scale_vsc"]:
        return "SCALE"
    if total >= st["mutate_vsc"]:
        return "MUTATE"
    return "KILL"


def valid_tz(name) -> bool:
    try:
        ZoneInfo(name)
        return bool(name)
    except Exception:
        return False


@cache.workspace_cached("tz", ttl=300)
def workspace_tz(ws: int) -> str:
    row = db.one("SELECT timezone FROM workspaces WHERE id = %s", (ws,))
    tz = (row or {}).get("timezone") or "UTC"
    return tz if valid_tz(tz) else "UTC"


def today_in(tz: str) -> date:
    return datetime.now(ZoneInfo(tz if valid_tz(tz) else "UTC")).date()


def today(ws: int) -> date:
    """The current date in the workspace's time zone (the server clock is irrelevant)."""
    return today_in(workspace_tz(ws))


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_grid(ws: int, start: date, days: int = 7):
    """Accounts x days grid with weekly totals and per-account decision."""
    st = get_settings(ws)
    end = start + timedelta(days=days - 1)
    accounts = db.query(
        "SELECT a.*, p.label AS phone_label, p.color AS phone_color, p.position AS phone_pos "
        "FROM accounts a LEFT JOIN phones p ON p.id = a.phone_id "
        "WHERE a.workspace_id = %s ORDER BY p.position NULLS LAST, p.id NULLS LAST, a.handle",
        (ws,),
    )
    views = {}
    for r in db.query(
        "SELECT account_id, day, views, source FROM daily_views "
        "WHERE workspace_id = %s AND day BETWEEN %s AND %s",
        (ws, start, end),
    ):
        views[(r["account_id"], r["day"])] = r

    # scored hooks inside the week -> "best hook?"
    best = set()
    for r in db.query(
        "SELECT DISTINCT account_id FROM posts WHERE workspace_id = %s AND status = 'posted' "
        "AND post_date BETWEEN %s AND %s "
        "AND v IS NOT NULL AND s IS NOT NULL AND c IS NOT NULL AND (v + s + c) >= %s",
        (ws, start, end, st["scale_vsc"]),
    ):
        best.add(r["account_id"])

    stalled, unverified = stall_scan(ws, st)
    day_list = [start + timedelta(days=i) for i in range(days)]
    rows = []
    for a in accounts:
        cells, total = [], 0
        for d in day_list:
            cell = views.get((a["id"], d))
            n = cell["views"] if cell else None
            total += n or 0
            cells.append({"day": d.isoformat(), "views": n, "source": cell["source"] if cell else None})
        best_hook = a["id"] in best
        decision, reason = account_decision(a, total, best_hook, a["id"] in stalled, st, a["id"] in unverified)
        rows.append({**a, "created_at": None, "cells": cells, "weekly_total": total,
                     "best_hook": best_hook, "decision": decision, "reason": reason})
    day_totals = [sum((r["cells"][i]["views"] or 0) for r in rows) for i in range(days)]
    grand = sum(day_totals)
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "days": [d.isoformat() for d in day_list],
        "rows": rows, "day_totals": day_totals, "grand_total": grand,
        "avg_per_account": round(grand / len(rows)) if rows else 0,
        "settings": st,
    }


def stall_scan(ws: int, st) -> tuple[set, set]:
    """(stalled, unverified). An account is only stalled when its last N posted posts all have
    recorded views and none broke `stall_views`. If any of them never had views recorded, the
    0s are just missing data, so the account is `unverified` instead of being sent to KILL."""
    n = st["kill_after_posts"]
    rows = db.query(
        "SELECT account_id, views, views_updated_at FROM ("
        "  SELECT account_id, views, views_updated_at, ROW_NUMBER() OVER (PARTITION BY account_id "
        "    ORDER BY post_date DESC, id DESC) AS rn FROM posts WHERE workspace_id = %s AND status = 'posted'"
        ") t WHERE rn <= %s",
        (ws, n),
    )
    agg = {}
    for r in rows:
        cnt, mx, untracked = agg.get(r["account_id"], (0, 0, 0))
        agg[r["account_id"]] = (cnt + 1, max(mx, r["views"]), untracked + (r["views_updated_at"] is None))
    stalled, unverified = set(), set()
    for acc, (cnt, mx, untracked) in agg.items():
        if cnt >= n and mx < st["stall_views"]:
            (unverified if untracked else stalled).add(acc)
    return stalled, unverified


def stalled_accounts(ws: int, st) -> set:
    return stall_scan(ws, st)[0]


def account_decision(a, weekly_total, best_hook, is_stalled, st, unverified=False):
    if a["status"] == "killed":
        return "KILLED", "Marked killed"
    if a["status"] in ("paused", "warming"):
        return "—", a["status"].capitalize()
    if is_stalled:
        return "KILL", f"No post above {st['stall_views']:,} views in the last {st['kill_after_posts']} posts"
    if unverified and weekly_total < st["scale_weekly"]:
        return "HOLD", (f"Not enough data to judge: some of the last {st['kill_after_posts']} posts have no "
                        f"views recorded. Add their views to enable the Stop check")
    if weekly_total >= st["scale_weekly"] and best_hook:
        return "SCALE", f"{weekly_total:,} views this week with a top-scoring hook"
    if weekly_total >= st["scale_weekly"]:
        return "HOLD", f"{weekly_total:,} views but no hook scored {st['scale_vsc']}+ yet — score your hooks"
    return "HOLD", ""


@cache.workspace_cached("dashboard", ttl=60)
def dashboard(ws: int, today: date | None = None):
    today = today or globals()["today"](ws)
    st = get_settings(ws)
    this_week = week_grid(ws, monday(today))
    prev_week = week_grid(ws, monday(today) - timedelta(days=7))
    # last 8 weeks trend
    trend = []
    first = monday(today) - timedelta(days=7 * 7)
    sums = {
        r["wk"]: r["v"]
        for r in db.query(
            "SELECT date_trunc('week', day)::date AS wk, SUM(views)::bigint AS v "
            "FROM daily_views WHERE workspace_id = %s AND day >= %s GROUP BY 1", (ws, first))
    }
    for i in range(8):
        wk = first + timedelta(days=7 * i)
        trend.append({"week": wk.isoformat(), "views": int(sums.get(wk, 0))})

    by_phone = {}
    for r in this_week["rows"]:
        key = r["phone_label"] or "No phone"
        p = by_phone.setdefault(key, {"phone": key, "color": r["phone_color"] or "#94a3b8", "views": 0})
        p["views"] += r["weekly_total"]

    perf = performance(ws, today)
    cand = db.query(
        "SELECT p.id, p.hook, p.views, p.comments, p.likes, p.saves, p.shares, p.intent, p.v, p.s, p.c, a.handle "
        "FROM posts p LEFT JOIN accounts a ON a.id = p.account_id "
        "WHERE p.workspace_id = %s AND p.status = 'posted' AND p.hook <> '' "
        "ORDER BY p.views DESC LIMIT 200", (ws,))
    for h in cand:
        h.update({k: v for k, v in (perf.get(h["id"]) or {}).items() if k in ("label", "multiple", "intent_per_1k", "high_intent")})
        h["vsc"] = vsc_total(h["v"], h["s"], h["c"])
    judged = [h for h in cand if h.get("multiple") is not None and h.get("label") != "TOO_EARLY"]
    # winners first (by how far they beat their own account's normal), then raw views for the rest
    top_hooks = sorted(judged, key=lambda h: -h["multiple"])[:5] if len(judged) >= 3 else cand[:5]
    upcoming = db.query(
        "SELECT p.id, p.post_date, p.hook, p.status, a.handle FROM posts p "
        "LEFT JOIN accounts a ON a.id = p.account_id "
        "WHERE p.workspace_id = %s AND p.status IN ('planned','idea','scripted','filmed') AND p.post_date >= %s "
        "ORDER BY p.post_date LIMIT 6",
        (ws, today),
    )
    unscored = db.one(
        "SELECT COUNT(*) AS n FROM posts WHERE workspace_id = %s AND status='posted' "
        "AND (v IS NULL OR s IS NULL OR c IS NULL)", (ws,)
    )["n"]
    counts = {"SCALE": 0, "HOLD": 0, "KILL": 0}
    for r in this_week["rows"]:
        if r["decision"] in counts:
            counts[r["decision"]] += 1

    prev_by_id = {r["id"]: r["weekly_total"] for r in prev_week["rows"]}
    movers = []
    for r in this_week["rows"]:
        prev = prev_by_id.get(r["id"], 0)
        cur = r["weekly_total"]
        delta = cur - prev
        pct = round(delta / prev * 100) if prev else (100 if cur else 0)
        movers.append({
            "id": r["id"],
            "handle": r["handle"],
            "views": cur,
            "prev_views": prev,
            "delta": delta,
            "pct": pct,
            "decision": r["decision"],
        })
    movers.sort(key=lambda x: -abs(x["delta"]))
    n_accounts = db.one(
        "SELECT COUNT(*) AS n FROM accounts WHERE workspace_id=%s", (ws,)
    )["n"]
    has_views = bool(db.one(
        "SELECT 1 AS x FROM daily_views WHERE workspace_id=%s LIMIT 1", (ws,)
    ))
    scored_hooks = db.one(
        "SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s AND v IS NOT NULL AND s IS NOT NULL AND c IS NOT NULL",
        (ws,),
    )["n"]
    n_posts = db.one(
        "SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s", (ws,)
    )["n"]
    # videos the sync pulled in count as logged views, and a few of them are enough to start deciding
    n_synced = db.one(
        "SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s AND external_id IS NOT NULL", (ws,))["n"]
    has_views = has_views or n_synced > 0
    scored_hooks = scored_hooks + (n_synced if n_synced >= 3 else 0)
    return {
        "week_start": this_week["start"],
        "week_views": this_week["grand_total"],
        "today_views": sum((r["cells"][(today - monday(today)).days]["views"] or 0) for r in this_week["rows"]),
        "week_estimated": any(c.get("source") == "estimated" for r in this_week["rows"] for c in r["cells"]),
        "prev_week_views": prev_week["grand_total"],
        "accounts": n_accounts,
        "avg_per_account": this_week["avg_per_account"],
        "decisions": counts,
        "trend": trend,
        "by_phone": list(by_phone.values()),
        "leaders": sorted(
            [{"handle": r["handle"], "views": r["weekly_total"], "decision": r["decision"]}
             for r in this_week["rows"]], key=lambda x: -x["views"])[:5],
        "act_on": [
            {"handle": r["handle"], "decision": r["decision"], "reason": r["reason"]}
            for r in this_week["rows"] if r["decision"] in ("SCALE", "KILL")],
        "movers": movers[:8],
        "top_hooks": top_hooks,
        "upcoming": upcoming,
        "unscored_posts": unscored,
        "settings": st,
        "setup": {
            "has_accounts": n_accounts > 0,
            "has_views": has_views,
            "has_posts": n_posts > 0,
            "has_scored_hook": scored_hooks > 0,
            "complete": n_accounts > 0 and has_views and scored_hooks > 0,
        },
    }



def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


# ---------- the hook score engine ----------
# A hook score (V + S + C, each 0-10, total /30) calculated from what actually happened, never from the wording.
# Each part is the post's result against ITS OWN account's normal, on a log scale: 1x normal = 5, 2x = 7.5, 4x+ = 10,
# half = 2.5, a quarter or less = 0. Built the way outlier / creative-scoring tools work (a video against its own
# baseline, with a minimum-data gate), using the signals the sync provides:
#   V  Pull         views vs the account's normal views              (did the opening stop the scroll and travel?)
#   S  Resonance    saves + shares per view vs the account's normal  (did people want to keep or pass it on?)
#   C  Conversion   comments per view vs normal, plus buy/ask comments (did it make people act?)
ENGINE_MIN_VIEWS = 100          # fewer views than this and a rate is noise
ENGINE_NEUTRAL = 5              # used for a part that has no baseline yet (flagged, and confidence drops to low)
_CONF = {"low": 0.4, "medium": 0.7, "high": 0.9}


def _score_part(multiple):
    """1x normal = 5; each doubling adds 2.5; clamped to 0..10."""
    if multiple is None:
        return None
    if multiple <= 0:
        return 0
    from math import log2
    return int(round(max(0.0, min(10.0, 5 + 2.5 * log2(multiple)))))


def engine_scores(pf: dict) -> dict | None:
    """{v, s, c, total, confidence, reasoning, diagnosis} for a measured post, else None.

    Needs a judged post (WINNER / AVERAGE / DUD): too-young posts and posts with no baseline get no score, because
    guessing would be exactly the wording-only scoring that did not separate winners from duds.
    """
    if not pf or pf.get("label") not in ("WINNER", "AVERAGE", "DUD") or pf.get("multiple") is None:
        return None
    v = _score_part(pf["multiple"])
    s = _score_part(pf.get("_res_multiple"))
    c = _score_part(pf.get("_conv_multiple"))
    assumed = []
    if s is None:
        s = ENGINE_NEUTRAL
        assumed.append("resonance")
    if c is None:
        c = ENGINE_NEUTRAL
        assumed.append("conversion")
    boost = ""
    share = pf.get("intent_share")
    if share is not None and share >= 10 and c < 10:
        c, boost = min(10, c + 2), f" (+2: {share}% of comments ask to buy or where to get it)"
    why = [f"Pull {v}/10: {pf['multiple']}x this account's normal views",
           f"Resonance {s}/10: " + ("no saves/shares baseline yet, assumed average" if "resonance" in assumed
                                    else f"saves+shares {pf['_res_multiple']:.1f}x normal"),
           f"Conversion {c}/10: " + (("no comments baseline yet, assumed average" if "conversion" in assumed
                                     else f"comments {pf['_conv_multiple']:.1f}x normal") + boost)]
    if v >= 7 and s <= 4:
        diag = "People watch but do not keep or share it: the opening works, rework the body or the payoff."
    elif v <= 4 and (s >= 6 or c >= 6):
        diag = "Few people saw it, but those who did acted: the idea works, rework the first 3 seconds."
    elif v <= 4 and s <= 4 and c <= 4:
        diag = "Weak on every signal: change the angle, not just the wording."
    elif v >= 7 and s >= 6:
        diag = "Strong reach and strong resonance: repeat this angle."
    else:
        diag = ""
    conf = "low" if assumed else (pf.get("confidence") or "low")
    return {"v": v, "s": s, "c": c, "total": v + s + c, "confidence": conf,
            "reasoning": "; ".join(why) + ".", "diagnosis": diag}


def apply_auto_scores(ws: int, perf: dict | None = None) -> int:
    """Write the engine's score onto posts that have no human score. Returns how many posts changed.

    Never touches a score typed by a person ('manual'), imported from a sheet with scores ('imported') or made by the old AI
    ('ai'); it only creates and refreshes its own ('auto'), because results keep arriving after each sync.
    """
    perf = perf if perf is not None else performance(ws)
    ids = [pid for pid, pf in perf.items() if engine_scores(pf)]
    if not ids:
        return 0
    mine = {r["id"]: r for r in db.query(
        "SELECT id, v, s, c, score_by FROM posts WHERE workspace_id=%s AND id = ANY(%s) "
        "AND (score_by IS NULL OR score_by IN ('', 'auto') "
        "     OR (score_by = 'imported' AND v IS NULL AND s IS NULL AND c IS NULL))",   # a sync tags its posts 'imported'
        (ws, ids))}
    n = 0
    for pid, row in mine.items():
        e = engine_scores(perf[pid])
        if (row["v"], row["s"], row["c"], row["score_by"]) == (e["v"], e["s"], e["c"], "auto"):
            continue                                    # unchanged since the last sync: no churn
        conf = _CONF[e["confidence"]]
        db.execute(
            "UPDATE posts SET v=%s, s=%s, c=%s, score_by='auto', score_confidence=%s, score_reasoning=%s, "
            "score_blind=FALSE WHERE id=%s AND workspace_id=%s",
            (e["v"], e["s"], e["c"], conf, e["reasoning"], pid, ws))
        record_score_snapshot(ws, pid, e["v"], e["s"], e["c"], conf, e["reasoning"], "", "auto", blind=False)
        n += 1
    return n


@cache.workspace_cached("performance", ttl=60)
def performance(ws: int, today: date | None = None, days: int = 180) -> dict:
    """post_id -> how that post did against ITS OWN account's normal, plus its engagement mix.

    multiple      views / median views of the account's other mature posts (None without a baseline)
    intent_per_1k (saves + shares + comments) per 1,000 views: people who wanted to act, not just watch
    reach         views / followers (how far past the audience it travelled; needs a follower snapshot)
    label         WINNER | AVERAGE | DUD | TOO_EARLY | NO_BASELINE
    """
    today = today or globals()["today"](ws)
    st = get_settings(ws)
    since = today - timedelta(days=days)
    rows = db.query(
        """SELECT p.id, p.account_id, p.post_date, p.views, p.likes, p.comments, p.shares, p.saves, p.duration_s,
                  p.date_estimated, p.views_updated_at, a.platform,
                  p.comments_fetched, p.buy_comments, p.ask_comments, p.more_comments
           FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id = %s AND p.status IN ('posted', 'analysed') AND p.post_date >= %s
             AND p.views_updated_at IS NOT NULL
           ORDER BY p.account_id, p.post_date DESC, p.id DESC""",
        (ws, since))
    snaps = {}
    for r in db.query("SELECT account_id, day, followers FROM account_snapshots "
                      "WHERE workspace_id = %s AND followers IS NOT NULL ORDER BY day", (ws,)):
        snaps.setdefault(r["account_id"], []).append((r["day"], r["followers"]))

    # lifetime views per post per day, as the sync saw them: the raw material for an early read
    series = {}
    for r in db.query("SELECT post_id, day, views FROM post_view_snapshots WHERE workspace_id = %s AND day >= %s "
                      "ORDER BY post_id, day", (ws, since)):
        series.setdefault(r["post_id"], []).append((r["day"], r["views"]))

    def early_views(r):
        """Views as of the end of the day after posting (the first ~24-48h), from the last snapshot inside that window."""
        if r["date_estimated"]:
            return None
        best = None
        for day, v in series.get(r["id"], []):
            off = (day - r["post_date"]).days
            if 0 <= off <= EARLY_WINDOW_DAYS:
                best = v
        return best

    def trajectory(r):
        return [[(day - r["post_date"]).days, v] for day, v in series.get(r["id"], [])
                if 0 <= (day - r["post_date"]).days <= 14][:15]

    def fmt_group(r):
        """YouTube channels mix Shorts and long-form, which are different products with different normals."""
        is_yt = (r["platform"] or "").lower().startswith("youtube")
        return "short" if is_yt and r["duration_s"] is not None and r["duration_s"] <= SHORTS_MAX_SECONDS else "main"

    by_acc = {}
    for r in rows:
        by_acc.setdefault((r["account_id"], fmt_group(r)), []).append(r)

    def age(r):
        return None if r["date_estimated"] else (today - r["post_date"]).days

    def intent(r):
        v = r["views"] or 0
        if v < 100:                                    # too few views for a rate to mean anything
            return None
        return ((r["saves"] or 0) + (r["shares"] or 0) + (r["comments"] or 0)) / v * 1000.0

    def followers_at(acc, r):
        """The audience size at (or just before) posting; the first snapshot when the account was measured later."""
        found = None
        for day, f in snaps.get(acc, []):
            if day <= r["post_date"]:
                found = f
        if found is None and snaps.get(acc):
            found = snaps[acc][0][1]
        return found

    out = {}
    for (acc, _fmt), posts in by_acc.items():
        mature = [r for r in posts if (age(r) is None or age(r) >= MATURE_DAYS)]
        recent = mature[:BASELINE_POSTS + 1]
        acc_intent = _median(intent(r) for r in mature)

        def res_rate(r):
            v_ = r["views"] or 0
            if v_ < ENGINE_MIN_VIEWS or (r["saves"] is None and r["shares"] is None):
                return None
            return ((r["saves"] or 0) + (r["shares"] or 0)) / v_

        def conv_rate(r):
            v_ = r["views"] or 0
            return None if v_ < ENGINE_MIN_VIEWS or r["comments"] is None else (r["comments"] or 0) / v_

        res_pool = [x for x in (res_rate(r) for r in mature) if x is not None]
        conv_pool = [x for x in (conv_rate(r) for r in mature) if x is not None]
        res_base = _median(res_pool) if len(res_pool) >= MIN_BASELINE else None
        conv_base = _median(conv_pool) if len(conv_pool) >= MIN_BASELINE else None
        early_pool = [e for e in (early_views(r) for r in mature) if e]
        early_base = _median(early_pool) if len(early_pool) >= MIN_BASELINE else None
        for r in posts:
            others = [x["views"] for x in recent if x["id"] != r["id"]][:BASELINE_POSTS]
            baseline = _median(others) if len(others) >= MIN_BASELINE else None
            a = age(r)
            views = r["views"] or 0
            multiple = (views / baseline) if baseline else None
            ev = early_views(r)
            early_multiple = (ev / early_base) if (ev and early_base) else None
            if a is not None and a < MATURE_DAYS:
                # not judged yet, but a fast start is the earliest signal there is: act on it while it is hot
                label = ("HOT_START" if early_multiple is not None and early_multiple >= st["winner_pct"] / 100.0
                         else "TOO_EARLY")
            elif baseline is None or not baseline:
                label = "NO_BASELINE"
            elif multiple >= st["winner_pct"] / 100.0:
                label = "WINNER"
            elif multiple <= DUD_MULTIPLE:
                label = "DUD"
            else:
                label = "AVERAGE"
            ipk = intent(r)
            followers = followers_at(acc, r)
            # secondary view: views per follower against the account's own median of that. It only says something
            # once the audience has been measured on two different days; before that it would just repeat `multiple`.
            reach_mult = None
            if followers and views and len(snaps.get(acc, [])) >= 2:
                theirs = []
                for x in recent:
                    fx = followers_at(acc, x)
                    if x["id"] != r["id"] and fx and x["views"]:
                        theirs.append(x["views"] / fx)
                theirs = theirs[:BASELINE_POSTS]
                if len(theirs) >= MIN_BASELINE and _median(theirs):
                    reach_mult = (views / followers) / _median(theirs)
            rate = lambda n: (round(n / views * 100, 2) if n is not None and views else None)
            fetched = r["comments_fetched"]
            intent_c = (r["buy_comments"] or 0) + (r["ask_comments"] or 0)
            n_base = len(others)
            lvl_n = 2 if n_base >= 15 else 1 if n_base >= 8 else 0
            lvl_a = 2 if (a is None or a >= SETTLED_DAYS) else 1 if a >= 4 else 0
            out[r["id"]] = {
                "n_baseline": n_base, "confidence": ("low", "medium", "high")[min(lvl_n, lvl_a)],
                "settled": bool(a is None or a >= SETTLED_DAYS), "recent_duds": 0,
                "early_views": ev, "early_baseline": round(early_base) if early_base else None,
                "early_multiple": round(early_multiple, 2) if early_multiple is not None else None,
                "trajectory": trajectory(r),
                "comments_read": fetched, "buy_comments": r["buy_comments"], "ask_comments": r["ask_comments"],
                "more_comments": r["more_comments"], "intent_comments": intent_c if fetched else None,
                "intent_share": round(intent_c / fetched * 100, 1) if fetched and fetched >= MIN_COMMENTS_FOR_SHARE else None,
                "label": label, "age_days": a,
                "baseline": round(baseline) if baseline else None,
                "multiple": round(multiple, 2) if multiple is not None else None,
                "intent_per_1k": round(ipk, 1) if ipk is not None else None,
                "high_intent": bool(ipk is not None and acc_intent and ipk >= acc_intent * HIGH_INTENT_MULTIPLE),
                "like_rate": rate(r["likes"]), "comment_rate": rate(r["comments"]),
                "share_rate": rate(r["shares"]), "save_rate": rate(r["saves"]),
                "followers": followers,
                "reach": round(views / followers, 2) if followers else None,
                "reach_multiple": round(reach_mult, 2) if reach_mult is not None else None,
                "growth_note": _growth_note(multiple, reach_mult),
                "_res_multiple": (res_rate(r) / res_base) if res_base and res_rate(r) is not None else None,
                "_conv_multiple": (conv_rate(r) / conv_base) if conv_base and conv_rate(r) is not None else None,
            }
    # one weak post is not a reason to stop: count the weak ones among this post and the two before it
    for (acc, _fmt), posts in by_acc.items():
        judged = [r for r in posts if (age(r) is None or age(r) >= MATURE_DAYS)]
        for i, r in enumerate(judged):
            out[r["id"]]["recent_duds"] = sum(1 for x in judged[i:i + 3] if out[x["id"]]["label"] == "DUD")
    return out


def _growth_note(multiple, reach_multiple) -> str:
    """When the raw and the per-follower reading disagree by a clear margin, say why. Never changes the call."""
    if multiple is None or reach_multiple is None or multiple <= 0:
        return ""
    if multiple >= 1.2 and reach_multiple <= multiple * 0.75:
        return (f"Part of this is audience growth: it did {multiple}x in views, but {reach_multiple}x relative to the "
                f"audience the account had when it was posted.")
    if reach_multiple >= multiple * 1.3 and reach_multiple >= 1.2:
        return (f"Better than it looks: {multiple}x in views, but {reach_multiple}x relative to the audience the "
                f"account had at the time.")
    return ""


def describe_performance(pf: dict | None, post: dict | None, quotes: list, short: bool = False) -> str:
    """One line of measured evidence for a prompt, or '' when there is nothing trustworthy to say yet."""
    if not pf or pf["label"] in ("TOO_EARLY", "NO_BASELINE") or pf["multiple"] is None:
        if pf and pf["label"] == "HOT_START" and pf["early_multiple"] is not None:
            return f"fast start: first day {pf['early_multiple']}x this account's usual first day (too young to judge fully)"
        return ""
    bits = [f"{pf['multiple']}x this account's normal of {pf['baseline']:,} views ({pf['label'].lower()})"]
    if post:
        for k, word in (("likes", "likes"), ("saves", "saves"), ("shares", "shares")):
            if post.get(k):
                bits.append(f"{post[k]:,} {word}")
    if pf["intent_per_1k"] is not None:
        bits.append(f"{pf['intent_per_1k']} saves+shares+comments per 1k views")
    if not short and pf.get("comments_read"):
        bits.append(f"{pf['intent_comments']} of {pf['comments_read']} comments ask to buy it or how/where to get it")
    if not short and quotes:
        bits.append("people wrote: " + " / ".join(f'"{q}"' for q in quotes))
    return "; ".join(bits)


def _intent_quotes(ws: int, post_ids: list, per_post: int = 2) -> dict:
    """The most-liked buy/ask comments per post, trimmed: the audience's own words for what they wanted."""
    if not post_ids:
        return {}
    out = {}
    for r in db.query(
            """SELECT post_id, body FROM (
                 SELECT post_id, body, row_number() OVER (PARTITION BY post_id ORDER BY score DESC, likes DESC NULLS LAST) rn
                 FROM post_comments WHERE workspace_id = %s AND post_id = ANY(%s) AND kind IN ('buy', 'ask')) t
               WHERE rn <= %s""", (ws, post_ids, per_post)):
        text = " ".join((r["body"] or "").split())[:90]
        out.setdefault(r["post_id"], []).append(text.replace('"', "'"))
    return out


def winner_examples(ws: int, account_id: int | None = None, n: int = 8, n_losers: int = 5):
    """(winners, losers) as prompt-ready dicts, judged by performance against each account's own normal.
    Falls back to raw views for workspaces that do not have enough history for a baseline yet."""
    perf = performance(ws)
    rows = db.query(
        """SELECT p.id, p.hook, p.views, p.comments, p.likes, p.saves, p.shares, a.handle, p.account_id
           FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id = %s AND p.status IN ('posted', 'analysed') AND p.hook <> ''
             AND p.views_updated_at IS NOT NULL""", (ws,))
    judged = []
    for r in rows:
        pf = perf.get(r["id"])
        if not pf or pf["multiple"] is None or pf["label"] == "TOO_EARLY":
            continue
        bits = [f"{r['views']:,} views", f"{pf['multiple']}x this account's normal"]
        if r["saves"]:
            bits.append(f"{r['saves']:,} saves")
        if r["shares"]:
            bits.append(f"{r['shares']:,} shares")
        if pf["intent_per_1k"] is not None:
            bits.append(f"{pf['intent_per_1k']} intent signals per 1k views")
        if pf["comments_read"]:
            bits.append(f"{pf['intent_comments']} of {pf['comments_read']} comments asked to buy it or how/where to get it")
        judged.append({**r, "note": ", ".join(bits), "multiple": pf["multiple"], "_pf": pf})
    if len(judged) >= 6:
        ranked = sorted(judged, key=lambda x: (-x["multiple"], -(x["_pf"]["intent_per_1k"] or 0)))

        def pick(label, descending, limit):
            """The account's own posts first (its audience, its niche); other accounts only fill what is left."""
            seq = ranked if descending else list(reversed(ranked))
            own = [x for x in seq if x["_pf"]["label"] == label and (account_id is None or x["account_id"] == account_id)]
            rest = [] if account_id is None else [
                {**x, "note": x["note"] + f", on {x['handle']}"} for x in seq
                if x["_pf"]["label"] == label and x["account_id"] != account_id]
            return (own + rest)[:limit]

        winners = pick("WINNER", True, n)
        quotes = _intent_quotes(ws, [w["id"] for w in winners])
        for w in winners:
            if quotes.get(w["id"]):
                w["note"] += "; people wrote: " + " / ".join(f'"{q}"' for q in quotes[w["id"]])
        losers = pick("DUD", False, n_losers)
        if not winners:                                  # nothing cleared the bar yet: the best of what there is
            winners = [x for x in ranked if account_id is None or x["account_id"] == account_id][:n] or ranked[:n]
        strip = lambda xs: [{k: v for k, v in x.items() if k != "_pf"} for x in xs]
        return strip(winners), strip(losers)
    byviews = sorted((r for r in rows), key=lambda r: -(r["views"] or 0))
    return ([{**r, "note": f"{r['views']:,} views"} for r in byviews[:n]],
            [{**r, "note": f"{r['views']:,} views"} for r in byviews[::-1][:n_losers]])


def _sure(pf) -> str:
    """' (12 posts, medium confidence)' for a measured call, or ''."""
    if pf and pf.get("n_baseline") is not None and pf.get("confidence"):
        return f" ({pf['n_baseline']} posts of its own, {pf['confidence']} confidence)"
    return ""


def _diag(pf) -> str:
    e = engine_scores(pf)
    return f". {e['diagnosis']}" if e and e["diagnosis"] else ""


def evidence_decision(total, st, pf) -> tuple[str, str]:
    """(decision, basis). What happened outranks what the hook score predicted.

      WINNER (beat the winner threshold)      -> SCALE   repeat it
      HOT_START (young, strong first day)     -> SCALE   act now
      above normal, below the threshold       -> TWEAK   it works: strengthen it
      about normal (0.7x to 1.2x)             -> HOLD    nothing to change
      below normal, above a dud (0.5x to 0.7x)-> TWEAK   rework it
      DUD (half its account's normal or less) -> TWEAK   one weak post: rework the angle, not the account
                                              -> STOP    only when 2 of its last 3 posts were weak too
    Unmeasured posts fall back to the hook score, which is then the only evidence there is.
    """
    if pf and pf.get("multiple") is not None and pf["label"] in ("WINNER", "AVERAGE", "DUD"):
        m, tail = pf["multiple"], _sure(pf)
        if pf["label"] == "WINNER":
            return "SCALE", f"Measured: {m}x this account's normal{tail}"
        if pf["label"] == "DUD":
            duds = pf.get("recent_duds")
            if duds is None or duds >= 2:        # no history given (older callers), or a pattern: stop it
                return "KILL", f"Measured: only {m}x this account's normal{tail}"
            return "MUTATE", (f"Measured: only {m}x this account's normal, but one weak post is not a pattern: "
                              f"rework the angle, not the account{tail}" + _diag(pf))
        hi = (st.get("hold_high_pct") or HOLD_HIGH * 100) / 100.0
        lo = (st.get("hold_low_pct") or HOLD_LOW * 100) / 100.0
        if m >= hi:
            return "MUTATE", f"Measured: {m}x this account's normal (above normal, so strengthen it){tail}" + _diag(pf)
        if m <= lo:
            return "MUTATE", f"Measured: {m}x this account's normal (below normal, so rework it){tail}" + _diag(pf)
        return "HOLD", f"Measured: {m}x this account's normal (about normal, nothing to change){tail}"
    if pf and pf["label"] == "HOT_START" and pf.get("early_multiple") is not None:
        return "SCALE", f"Fast start: {pf['early_multiple']}x this account's usual first day"
    base = post_decision(total, st)
    if base:
        why = "not measured yet" if not pf or pf["label"] in ("TOO_EARLY", "NO_BASELINE") else "unmeasured"
        return base, f"From the hook score {total}/30 ({why})"
    return "", ""


def apply_evidence(rows, perf: dict, st: dict):
    """Set decision/decision_basis/vsc_decision on post dicts in place (each has vsc and id). Returns rows."""
    for r in rows:
        total = r.get("vsc")
        r["vsc_decision"] = post_decision(total, st)
        r["decision"], r["decision_basis"] = evidence_decision(total, st, perf.get(r.get("id")))
    return rows


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


MIN_SCORED_FOR_SUGGESTION = 12


def blind_scores(ws: int) -> dict:
    """post_id -> total of the post's latest BLIND score (wording judged without knowing the result).

    Sources: an AI score made with the results withheld, or a human score (manual / imported), which cannot have
    seen results the scorer did not have. Informed and unknown-provenance AI scores are deliberately excluded.
    """
    out = {}
    for r in db.query(
            """SELECT DISTINCT ON (post_id) post_id, total FROM score_snapshots
               WHERE workspace_id = %s AND blind IS TRUE AND total IS NOT NULL
               ORDER BY post_id, created_at DESC""", (ws,)):
        out[r["post_id"]] = r["total"]
    for r in db.query(
            """SELECT id, v, s, c FROM posts WHERE workspace_id = %s AND v IS NOT NULL AND s IS NOT NULL
                  AND c IS NOT NULL AND score_by IN ('manual', 'imported')""", (ws,)):
        out.setdefault(r["id"], vsc_total(r["v"], r["s"], r["c"]))
    return out


def suggest_thresholds(ws: int) -> dict:
    """Fit the Scale/Tweak lines to THIS workspace: where do hooks that actually won tend to score, and where do
    the ones that did not? Uses only BLIND scores, so the answer is "does the wording predict results", not
    "does the scorer repeat back what it was told"."""
    st = get_settings(ws)
    perf = performance(ws)
    blind = blind_scores(ws)
    measured_ids = {pid for pid, pf in perf.items()
                    if pf["multiple"] is not None and pf["label"] in ("WINNER", "AVERAGE", "DUD")}
    pts = [(blind[pid], perf[pid]["label"]) for pid in measured_ids if pid in blind]
    any_score = {r["id"] for r in db.query(
        "SELECT id FROM posts WHERE workspace_id = %s AND v IS NOT NULL AND s IS NOT NULL AND c IS NOT NULL", (ws,))}
    without_blind = sorted(pid for pid in measured_ids if pid not in blind)
    excluded = len([pid for pid in measured_ids if pid in any_score and pid not in blind])
    win = [t for t, l in pts if l == "WINNER"]
    rest = [t for t, l in pts if l != "WINNER"]
    dud = [t for t, l in pts if l == "DUD"]

    def stats(xs):
        return {"n": len(xs), "median": round(_pct(xs, .5), 1) if xs else None,
                "p25": round(_pct(xs, .25), 1) if xs else None, "p75": round(_pct(xs, .75), 1) if xs else None}

    out = {"current": {"scale_vsc": st["scale_vsc"], "mutate_vsc": st["mutate_vsc"]},
           "scored_and_measured": len(pts),                       # blind-scored and measured: what the fit rests on
           "excluded_informed_or_unknown": excluded,              # scored, but not blind, so not trusted for a fit
           "measured_without_blind_score": len(without_blind),    # could be blind-scored to add evidence
           "by_result": {"winner": stats(win), "average": stats([t for t, l in pts if l == "AVERAGE"]), "dud": stats(dud)},
           "enough_data": False, "suggestion": None, "message": ""}
    if len(pts) < MIN_SCORED_FOR_SUGGESTION or len(win) < 3 or len(rest) < 3:
        extra = (f" {excluded} more posts have scores that saw their own results or are of unknown origin, so they "
                 "are not used." if excluded else "")
        out["message"] = (f"Needs at least {MIN_SCORED_FOR_SUGGESTION} measured posts with a wording-only score, "
                          f"including 3 winners and 3 non-winners. You have {len(pts)} ({len(win)} winners)."
                          f"{extra}"
                          + (f" {len(without_blind)} measured posts can be scored on their wording now." if without_blind else ""))
        return out

    def quality(scale):
        hit = [t for t, _ in pts if t >= scale]
        won = [t for t in win if t >= scale]
        return {"flagged_as_scale": len(hit),
                "of_which_winners": len(won),
                "precision": round(len(won) / len(hit) * 100) if hit else None,
                "winners_caught": round(len(won) / len(win) * 100)}

    # Scale line: low enough that most winners clear it, but never at or below where the typical non-winner scores.
    scale = max(round(_pct(win, .25)), round(_pct(rest, .5)) + 1)
    scale = min(scale, 30)
    # Tweak line: the typical non-dud. Below it a hook is both unproven and below what usually works.
    mutate = round(_pct([t for t, l in pts if l != "DUD"] or rest, .5))
    mutate = max(1, min(mutate, scale - 1))
    gap = (_pct(win, .5) or 0) - (_pct(rest, .5) or 0)
    out.update({
        "enough_data": True,
        "suggestion": {"scale_vsc": scale, "mutate_vsc": mutate},
        "separation": round(gap, 1),
        "quality_now": quality(st["scale_vsc"]),
        "quality_suggested": quality(scale),
        "message": ("Judged on wording alone, your hooks that won scored "
                    f"{round(gap, 1)} points higher than the rest, so the score does predict results." if gap >= 2 else
                    "Judged on wording alone, your winners score within 2 points of the rest, so the wording is not "
                    "what separates them (or there are too few posts to tell). Trust measured results over the score."),
    })
    return out


def decision_preview(ws: int, scale_vsc: int | None = None, mutate_vsc: int | None = None) -> dict:
    """Recompute post decisions under hypothetical VSC thresholds (scored posts only)."""
    st_now = get_settings(ws)
    st = dict(st_now)
    if scale_vsc is not None:
        st["scale_vsc"] = int(scale_vsc)
    if mutate_vsc is not None:
        st["mutate_vsc"] = int(mutate_vsc)
    rows = db.query(
        """SELECT p.id, a.handle, p.hook, p.v, p.s, p.c, p.views, p.post_date, p.score_by
           FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.workspace_id=%s AND p.v IS NOT NULL AND p.s IS NOT NULL AND p.c IS NOT NULL
           ORDER BY p.post_date DESC, p.id DESC LIMIT 500""",
        (ws,),
    )
    out = []
    for r in rows:
        total = vsc_total(r["v"], r["s"], r["c"])
        current = post_decision(total, st_now)
        simulated = post_decision(total, st)
        out.append({
            "id": r["id"],
            "handle": r["handle"],
            "hook": r["hook"],
            "post_date": r["post_date"].isoformat() if r["post_date"] else None,
            "vsc": total,
            "views": r["views"],
            "decision_now": current,
            "decision_if": simulated,
            "changed": current != simulated,
        })
    return {
        "thresholds": {"scale_vsc": st["scale_vsc"], "mutate_vsc": st["mutate_vsc"]},
        "thresholds_now": {"scale_vsc": st_now["scale_vsc"], "mutate_vsc": st_now["mutate_vsc"]},
        "posts": out,
        "changed": sum(1 for x in out if x["changed"]),
        "total_scored": len(out),
    }


def record_score_snapshot(ws: int, post_id: int, v, s, c, confidence, reasoning, notes, score_by: str,
                          blind: bool | None = None):
    """Persist score + decision under current thresholds for later comparison."""
    st = get_settings(ws)
    total = vsc_total(v, s, c)
    decision = post_decision(total, st) or ""
    db.execute(
        """INSERT INTO score_snapshots
           (workspace_id, post_id, v, s, c, total, decision, confidence, reasoning, notes, score_by, thresholds, blind)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (ws, post_id, v, s, c, total, decision, confidence, reasoning or "", notes or "", score_by,
         db.Json({"scale_vsc": st["scale_vsc"], "mutate_vsc": st["mutate_vsc"],
                  "scale_weekly": st.get("scale_weekly"), "kill_after_posts": st.get("kill_after_posts")}), blind),
    )


def score_history(ws: int, post_id: int) -> list:
    return db.query(
        """SELECT id, v, s, c, total, decision, confidence, reasoning, notes, score_by, thresholds, created_at
           FROM score_snapshots WHERE workspace_id=%s AND post_id=%s
           ORDER BY created_at DESC LIMIT 50""",
        (ws, post_id),
    )


def log_decisions(ws: int) -> int:
    """Remember today's call for every young-enough measured post, so it can be checked weeks later."""
    st = get_settings(ws)
    perf = performance(ws)
    today_ = today(ws)
    n = 0
    for pid, pf in perf.items():
        age_ = pf.get("age_days")
        if age_ is None or not (1 <= age_ <= 45):
            continue
        d, _why = evidence_decision(None, st, pf)
        if not d:
            continue
        n += db.execute(
            """INSERT INTO decision_log (post_id, workspace_id, day, age_days, multiple, label, decision, n_baseline, confidence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (post_id, day) DO UPDATE SET multiple=EXCLUDED.multiple, label=EXCLUDED.label,
                 decision=EXCLUDED.decision, n_baseline=EXCLUDED.n_baseline, confidence=EXCLUDED.confidence""",
            (pid, ws, today_, age_, pf.get("multiple"), pf["label"], d, pf.get("n_baseline"), pf.get("confidence") or ""))
    return n


def calibration(ws: int) -> dict:
    """Of the posts called SCALE around day 3, how many were still beating their account's normal around day 30?"""
    rows = db.query("SELECT post_id, age_days, multiple, label, decision, confidence FROM decision_log WHERE workspace_id=%s",
                    (ws,))
    st = get_settings(ws)
    threshold = st["winner_pct"] / 100.0
    by_post: dict = {}
    for r in rows:
        by_post.setdefault(r["post_id"], []).append(r)

    def nearest(items, lo, hi, target):
        c = [x for x in items if lo <= x["age_days"] <= hi]
        return min(c, key=lambda x: abs(x["age_days"] - target)) if c else None

    pairs = []
    for items in by_post.values():
        early, late = nearest(items, 2, 4, 3), nearest(items, 28, 45, 30)
        if early and late and late["multiple"] is not None:
            pairs.append((early, late))
    called = [(e, l) for e, l in pairs if e["decision"] == "SCALE"]
    held = [(e, l) for e, l in called if float(l["multiple"]) >= threshold]
    out = {"posts_checked": len(pairs), "called_scale_early": len(called), "still_winning_later": len(held),
           "hit_rate": round(len(held) / len(called), 2) if called else None,
           "needs": "Needs posts that were measured around day 3 and again around day 30."}
    if called:
        by_conf = {}
        for e, l in called:
            c = by_conf.setdefault(e["confidence"] or "unknown", {"called": 0, "held": 0})
            c["called"] += 1
            c["held"] += 1 if float(l["multiple"]) >= threshold else 0
        out["by_confidence"] = by_conf
    return out
