"""Inspiration in the flow: saved reference hooks become cards, and attach to cards as part of their brief.

An inspiration item is somebody else's hook. "Use this" makes an Idea card from it (labelled with where it came from);
any card can also have items attached, which then show in its brief next to the hook's own data.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from . import db, logic

IDEA_DUE_DAYS = 7


class InspireError(Exception):
    pass


def _row(ws: int, iid: int) -> dict:
    r = db.one("SELECT * FROM inspiration WHERE id=%s AND workspace_id=%s", (iid, ws))
    if not r:
        raise InspireError("Inspiration item not found")
    return r


def _title(r: dict) -> str:
    return (r.get("hook_text") or r.get("title") or "").strip()


def use(ws: int, iid: int, account_id: Optional[int] = None) -> dict:
    """An Idea card made from an inspiration item. The item's text is the starting point, never the finished script."""
    r = _row(ws, iid)
    if account_id is not None:
        if not db.one("SELECT 1 FROM accounts WHERE id=%s AND workspace_id=%s", (account_id, ws)):
            raise InspireError("Unknown account")
    else:
        a = db.one("SELECT id FROM accounts WHERE workspace_id=%s AND status IN ('active','warming') ORDER BY id LIMIT 1", (ws,))
        if not a:
            raise InspireError("Add an account first: a card belongs to one of your accounts")
        account_id = a["id"]
    n = db.one("SELECT COUNT(*) AS n FROM post_inspiration pi JOIN posts p ON p.id = pi.post_id "
               "WHERE p.workspace_id=%s AND pi.inspiration_id=%s", (ws, iid))["n"] + 1
    where = " ".join(x for x in (r.get("platform") or "", r.get("source_url") or "") if x)
    note = (r.get("notes") or "").strip()
    prompt = ("Inspired by another creator's hook" + (f" ({where})" if where else "") + ". Make it yours: keep the move "
              "that works, change the subject and the words so it is not a copy." + (f" Notes: {note}" if note else ""))
    card = db.one(
        """INSERT INTO posts (workspace_id, account_id, post_date, status, ai_prompt, hook, body, caption,
                              views, comments, intent, score_by, variant_label)
           VALUES (%s,%s,%s,'idea',%s,%s,'','',0,0,false,'',%s) RETURNING id""",
        (ws, account_id, logic.today(ws) + timedelta(days=IDEA_DUE_DAYS), prompt[:500], _title(r)[:500], f"I{n}"))
    db.execute("INSERT INTO post_inspiration (post_id, inspiration_id, workspace_id) VALUES (%s,%s,%s)",
               (card["id"], iid, ws))
    return {"post_id": card["id"], "label": f"I{n}", "status": "idea"}


def attach(ws: int, pid: int, iid: int) -> None:
    _row(ws, iid)
    if not db.one("SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws)):
        raise InspireError("Post not found")
    db.execute("INSERT INTO post_inspiration (post_id, inspiration_id, workspace_id) VALUES (%s,%s,%s) "
               "ON CONFLICT DO NOTHING", (pid, iid, ws))


def detach(ws: int, pid: int, iid: int) -> None:
    db.execute("DELETE FROM post_inspiration WHERE post_id=%s AND inspiration_id=%s AND workspace_id=%s", (pid, iid, ws))


def for_post(ws: int, pid: int) -> list[dict]:
    """The items attached to a card, with a player where the platform allows one."""
    from . import baseline
    rows = db.query(
        """SELECT i.id, i.title, i.hook_text, i.notes, i.platform, i.source_url
           FROM post_inspiration pi JOIN inspiration i ON i.id = pi.inspiration_id
           WHERE pi.post_id=%s AND pi.workspace_id=%s ORDER BY pi.created_at""", (pid, ws))
    out = []
    for r in rows:
        out.append({"id": r["id"], "title": r["title"] or "", "hook": r["hook_text"] or "", "notes": r["notes"] or "",
                    "platform": r["platform"] or "", "source_url": r["source_url"] or "",
                    "embed": baseline.embed_for(r["platform"] or "", r["source_url"] or "", "")})
    return out
