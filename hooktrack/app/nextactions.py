"""What needs the user today, built from the data: the dashboard's queue."""
from __future__ import annotations

from datetime import timedelta

from . import db, followups, logic

STALE_DAYS = 14


def build(ws: int) -> list[dict]:
    from . import main as m

    st = logic.get_settings(ws)
    perf = logic.performance(ws)
    today = logic.today(ws)
    rows = db.query(m.POST_SELECT + "WHERE p.workspace_id=%s AND p.external_id IS NOT NULL "
                    "ORDER BY p.post_date DESC LIMIT 1000", (ws,))
    hooks = logic.apply_evidence([m._post_out(r, st) for r in rows], perf, st)
    made = followups.for_hooks(ws, [h["id"] for h in hooks])
    made = {**made, **{i: {} for i in followups.made_from(ws, [h["id"] for h in hooks])}}   # copies are not "to send"
    cutoff = today - timedelta(days=followups.SETTLE_DAYS)
    scale = [h for h in hooks if h.get("decision") == "SCALE" and h["id"] not in made]
    tweak = [h for h in hooks if h.get("decision") == "MUTATE" and h["id"] not in made]
    scale_ready = [h for h in scale if h["post_date"] <= cutoff]
    tweak_ready = [h for h in tweak if h["post_date"] <= cutoff]

    own = db.query("SELECT id, status, url, published_at, result_post_id, created_at, video_url, video_file "
                   "FROM posts WHERE workspace_id=%s AND external_id IS NULL", (ws,))
    stale_before = today - timedelta(days=STALE_DAYS)

    def n(pred):
        return sum(1 for r in own if pred(r))

    stale = n(lambda r: r["status"] in ("idea", "scripted", "planned") and r["created_at"]
              and r["created_at"].date() <= stale_before)
    overdue = db.one(
        "SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s AND external_id IS NULL AND post_date < %s "
        "AND status IN ('idea','scripted','planned','filmed') AND created_at::date < %s",
        (ws, today, today))["n"]
    unpublished = n(lambda r: r["status"] == "filmed" and not r["published_at"])
    nolink = n(lambda r: r["status"] == "posted" and not (r["url"] or "").strip())
    waiting = n(lambda r: r["status"] == "posted" and (r["url"] or "").strip() and not r["result_post_id"])
    ready = n(lambda r: r["status"] == "posted" and r["result_post_id"])

    items = [
        ("scale", len(scale_ready), "Scale hook{s} not sent to Posts yet", "#/hooks"),
        ("tweak", len(tweak_ready), "Tweak hook{s} not sent to Posts yet", "#/hooks"),
        ("unpublished", unpublished, "Filmed card{s} not published yet", "#/pipeline"),
        ("nolink", nolink, "Posted card{s} without a video link (their results cannot be found)", "#/pipeline"),
        ("results", ready, "Posted card{s} with results in: review and mark analysed", "#/pipeline"),
        ("overdue", overdue, "card{s} past {their} date: film, publish or move the date", "#/pipeline"),
        ("stale", stale, f"Idea / Scripted card{{s}} waiting {STALE_DAYS}+ days: film or delete", "#/pipeline"),
        ("waiting", waiting, "Posted card{s} waiting for the next sync to find the video", "#/pipeline"),
    ]
    out = []
    from . import hotstart
    try:
        hot = hotstart.hot_posts(ws)
    except Exception:
        hot = []
    if hot:
        out.append({"key": "hot", "count": len(hot), "href": "#/hooks",
                    "text": f"video{'' if len(hot) == 1 else 's'} breaking out right now: make a matching one while "
                            f"{'it is' if len(hot) == 1 else 'they are'} hot"})
    for key, count, text, href in items:
        if count:
            out.append({"key": key, "count": count, "href": href,
                        "text": text.replace("{s}", "" if count == 1 else "s").replace("{their}", "its" if count == 1 else "their")})
    growing = len([h for h in scale + tweak if h["post_date"] > cutoff])
    if growing:
        out.append({"key": "growing", "count": growing, "href": "#/hooks", "quiet": True,
                    "text": f"hook{'' if growing == 1 else 's'} with a Scale or Tweak decision still growing (wait ~3 days)"})
    return out


def pace(ws: int) -> dict:
    """The weekly target and how many videos reached Filmed this week (Monday to Sunday, workspace time)."""
    target = int(logic.get_settings(ws).get("weekly_target") or 0)
    start = logic.monday(logic.today(ws))
    done = db.one(
        "SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s AND external_id IS NULL AND filmed_at IS NOT NULL "
        "AND (filmed_at AT TIME ZONE %s)::date >= %s", (ws, logic.workspace_tz(ws), start))["n"]
    return {"target": target, "done": int(done), "week_start": str(start)}
