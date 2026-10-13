"""Send a hook's next step to Posts, when the user chooses to.

Hooks are measured, posted videos the sync pulled in. For a hook decided:
  SCALE            -> a Scripted post, linked to the winner, to film a matching video for (upload it, then move it on)
  TWEAK (MUTATE)   -> an Idea post: the same hook, to rework a little into a new script
Nothing is created on its own: the user presses "Post" on the hook. It is created once per decision per hook.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from . import db, logic

KIND = {"SCALE": ("scripted", "S"), "MUTATE": ("idea", "T")}


class FollowupError(Exception):
    pass


def _prompt(decision: str, basis: str, own: str) -> str:
    own = (own or "").strip()
    if decision == "SCALE":
        why = basis or "it beat its own account's normal"
        text = (f"Matching video for a winning hook ({why}). "
                "Film a new video in the same format as the winning hook video, then upload it here.")
    else:
        why = basis or "about normal"
        text = (f"Tweak a little ({why}). Keep what worked and change one thing: the first words, "
                "the first shot, or the angle. Write the new script from this.")
    if own:
        text += " Original notes: " + own
    return text[:1500]


def made_from(ws: int, post_ids: list[int]) -> dict:
    """{synced post id: {id, hook, generation}} for videos that are the result of a card the user made from another
    hook. generation 2 = a copy of an original; 3 = a copy of a copy. Used so a copy is not cloned again by accident."""
    if not post_ids:
        return {}
    rows = db.query(
        """SELECT c.result_post_id AS rid, COALESCE(c.cloned_from, c.followup_of) AS origin
           FROM posts c WHERE c.workspace_id=%s AND c.result_post_id = ANY(%s) AND COALESCE(c.cloned_from, c.followup_of) IS NOT NULL""",
        (ws, post_ids))
    out: dict = {}
    for r in rows:
        gen, cur, seen = 2, r["origin"], {r["rid"]}
        while cur and cur not in seen and gen < 12:
            seen.add(cur)
            up = db.one("""SELECT COALESCE(c.cloned_from, c.followup_of) AS origin FROM posts c
                           WHERE c.workspace_id=%s AND c.result_post_id=%s AND COALESCE(c.cloned_from, c.followup_of) IS NOT NULL
                           LIMIT 1""", (ws, cur))
            if not up:
                break
            gen, cur = gen + 1, up["origin"]
        h = db.one("SELECT hook FROM posts WHERE id=%s AND workspace_id=%s", (r["origin"], ws))
        out.setdefault(r["rid"], {"id": r["origin"], "hook": (h or {}).get("hook") or "", "generation": gen})
    return out


def for_hooks(ws: int, hook_ids: list[int]) -> dict:
    """{hook id: {id, status, label}} of the posts already made from each hook."""
    if not hook_ids:
        return {}
    rows = db.query(
        "SELECT id, followup_of, status, variant_label, verdict FROM posts WHERE workspace_id=%s AND followup_of = ANY(%s) "
        "ORDER BY id", (ws, hook_ids))
    out: dict = {}
    for r in rows:
        o = out.setdefault(r["followup_of"], {"made": 0, "judged": 0, "beat": 0})
        o.update({"id": r["id"], "status": r["status"], "label": r["variant_label"]})
        o["made"] += 1
        if r["verdict"]:
            o["judged"] += 1
            o["beat"] += 1 if r["verdict"] == "beat" else 0
    return out


SETTLE_DAYS = 3        # a video younger than this is still gathering views, so its decision can still flip
DUE_DAYS = {"scripted": 3, "idea": 7}   # a new card's date (its due date) is this many days out


def send_top(ws: int, limit: int = 5, account_id: Optional[int] = None) -> dict:
    """Send the strongest Scale hooks that have not been sent yet and have settled. Returns {sent: [...], skipped}."""
    from datetime import timedelta
    from . import main as m

    limit = max(1, min(int(limit), 20))
    st = logic.get_settings(ws)
    perf = logic.performance(ws)
    where, params = ["p.workspace_id = %s", "p.external_id IS NOT NULL"], [ws]
    if account_id:
        where.append("p.account_id = %s"); params.append(account_id)
    rows = db.query(m.POST_SELECT + "WHERE " + " AND ".join(where) + " ORDER BY p.post_date DESC LIMIT 1000", params)
    posts = logic.apply_evidence([m._post_out(r, st) for r in rows], perf, st)
    made = for_hooks(ws, [p["id"] for p in posts])
    copies = made_from(ws, [p["id"] for p in posts])          # a copy of something you already made is not auto-picked
    cutoff = logic.today(ws) - timedelta(days=SETTLE_DAYS)
    ready = [p for p in posts if p.get("decision") == "SCALE" and p["id"] not in made and p["id"] not in copies
             and p["post_date"] <= cutoff]
    ready.sort(key=lambda p: -((perf.get(p["id"]) or {}).get("multiple") or 0))
    sent = [create_for(ws, p["id"]) | {"hook_id": p["id"], "hook": p.get("hook") or ""} for p in ready[:limit]]
    return {"sent": sent, "waiting": len(ready) - len(sent),
            "growing": sum(1 for p in posts if p.get("decision") == "SCALE" and p["id"] not in made and p["post_date"] > cutoff)}


def create_for(ws: int, hook_id: int, account_id: Optional[int] = None) -> dict:
    """Make the Idea / Scripted post for one hook, for `account_id` (default: the hook's own account).
    Returns {created, post_id, status, label}."""
    from . import main as m                      # lazy: main imports this module

    row = db.one(m.POST_SELECT + "WHERE p.id=%s AND p.workspace_id=%s", (hook_id, ws))
    if not row:
        raise FollowupError("Hook not found")
    if row.get("external_id") is None:
        raise FollowupError("Only hooks pulled in by the sync can be sent to Posts")
    st = logic.get_settings(ws)
    perf = logic.performance(ws)
    p = logic.apply_evidence([m._post_out(row, st)], perf, st)[0]
    d = p.get("decision")
    if d not in KIND:
        raise FollowupError("Only Scale or Tweak hooks can be sent to Posts")
    target = account_id or p["account_id"]
    if not db.one("SELECT 1 FROM accounts WHERE id=%s AND workspace_id=%s", (target, ws)):
        raise FollowupError("Unknown account")
    existing = db.one("SELECT id, status, variant_label FROM posts WHERE workspace_id=%s AND followup_of=%s AND account_id=%s "
                      "ORDER BY id DESC LIMIT 1", (ws, hook_id, target))
    if existing and p.get("followup_decision") == d:
        return {"created": False, "post_id": existing["id"], "status": existing["status"], "label": existing["variant_label"]}

    status, letter = KIND[d]
    n = db.one("SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s AND followup_of=%s", (ws, hook_id))["n"] + 1
    new = db.one(
        """INSERT INTO posts (workspace_id, account_id, post_date, status, ai_prompt, hook, body, caption,
                              views, comments, intent, score_by, followup_of, cloned_from, variant_label)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,0,false,'',%s,%s,%s) RETURNING id""",
        (ws, target, logic.today(ws) + timedelta(days=DUE_DAYS.get(status, 3)), status, _prompt(d, p.get("decision_basis") or "", p.get("ai_prompt") or ""),
         p.get("hook") or "", p.get("body") or "", p.get("caption") or "", hook_id,
         hook_id if d == "SCALE" else None, f"{letter}{n}"))
    db.execute("UPDATE posts SET followup_decision=%s WHERE id=%s AND workspace_id=%s", (d, hook_id, ws))
    from . import activation
    activation.mark(ws, "hook_sent")
    out = {"created": True, "post_id": new["id"], "status": status, "label": f"{letter}{n}"}
    if d == "SCALE" and st.get("read_on_send"):
        out.update(_read_original(ws, hook_id))
    return out


def _read_original(ws: int, hook_id: int) -> dict:
    """Read the winner's video so the brief arrives with its first words and structure. Never fails the send."""
    from . import baseline
    try:
        if baseline.already_read(baseline._post_row(ws, hook_id)):
            return {"read": "already"}
        baseline.make_blueprint(ws, hook_id)
        return {"read": "done", "credits_est": 1}
    except Exception as e:                          # no key, no public link, provider down: the card is still made
        msg = getattr(e, "detail", None) or str(e)
        return {"read": "failed", "read_error": str(msg)[:160]}


VARIANT_CHANGES = (
    ("a", "Change the first words: say the same thing a different way, in the first 2 seconds."),
    ("b", "Change the first shot: open on something different, keep the words."),
    ("c", "Change the angle: keep the topic, flip who it is for or what is at stake."),
)


def make_variants(ws: int, card_id: int) -> dict:
    """Three Idea cards (a / b / c) from a Tweak card, each changing one thing, so the results say which change worked."""
    c = db.one("SELECT * FROM posts WHERE id=%s AND workspace_id=%s", (card_id, ws))
    if not c:
        raise FollowupError("Post not found")
    if c.get("external_id") is not None or not c.get("followup_of") or c.get("cloned_from"):
        raise FollowupError("Variants are made from a Tweak card on Posts")
    if c["status"] != "idea":
        raise FollowupError("Make variants while the card is still an Idea")
    existing = db.query("SELECT id, variant_label FROM posts WHERE workspace_id=%s AND parent_id=%s ORDER BY id", (ws, card_id))
    if existing:
        return {"created": False, "variants": [{"id": r["id"], "label": r["variant_label"]} for r in existing]}
    base = c.get("variant_label") or "T"
    out = []
    for letter, change in VARIANT_CHANGES:
        row = db.one(
            """INSERT INTO posts (workspace_id, account_id, post_date, status, ai_prompt, hook, body, caption,
                                  views, comments, intent, score_by, followup_of, parent_id, variant_label)
               VALUES (%s,%s,%s,'idea',%s,%s,%s,%s,0,0,false,'',%s,%s,%s) RETURNING id""",
            (ws, c["account_id"], logic.today(ws), f"Variant {letter}. {change}"[:500], c["hook"] or "", c["body"] or "",
             c["caption"] or "", c["followup_of"], card_id, f"{base}{letter}"))
        out.append({"id": row["id"], "label": f"{base}{letter}"})
    return {"created": True, "variants": out}
