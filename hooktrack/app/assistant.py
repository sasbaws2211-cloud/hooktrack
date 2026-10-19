"""The in-app guide: a floating chat that answers questions about HookTrack in plain words.

What it knows:
  * a written guide to every page and feature (assistant_kb.md, kept next to this file)
  * where the person is now, the pages they went through to get here, and what is on their screen (sent by the browser)
  * a few facts about their workspace (do they have accounts, synced videos, scores, scheduled posts, a plan...)

It is only opened by someone who is already stuck, so it never greets with a tour: it works out what is most likely
confusing from the trail and the screen, and answers that first.
"""
from __future__ import annotations

import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from . import ai, db

KB_PATH = Path(__file__).parent / "assistant_kb.md"
_kb_cache: Optional[str] = None

MAX_TURNS = 12              # earlier turns are dropped: the guide only needs the recent thread
MAX_MESSAGE_CHARS = 1500
MAX_SCREEN_CHARS = 2500
MAX_TRAIL = 8
DAILY_LIMIT = 60            # messages per person per rolling 24 hours (a guard against runaway cost, not a product limit)
_hits: dict = defaultdict(deque)
_lock = threading.Lock()


def knowledge() -> str:
    global _kb_cache
    if _kb_cache is None:
        _kb_cache = KB_PATH.read_text(encoding="utf-8")
    return _kb_cache


def allow(user_id: int) -> bool:
    """True if this person may send another message now."""
    now = time.time()
    with _lock:
        q = _hits[user_id]
        while q and now - q[0] > 86400:
            q.popleft()
        if len(q) >= DAILY_LIMIT:
            return False
        q.append(now)
        return True


def workspace_facts(ws: int, user_id: int) -> dict:
    """A handful of true facts about this workspace so the answer fits their situation (no personal data)."""
    r = db.one(
        """SELECT
             (SELECT count(*) FROM accounts WHERE workspace_id=%(w)s) AS accounts,
             (SELECT count(*) FROM accounts WHERE workspace_id=%(w)s AND last_synced_at IS NOT NULL) AS synced_accounts,
             (SELECT count(*) FROM posts WHERE workspace_id=%(w)s AND external_id IS NOT NULL) AS synced_videos,
             (SELECT count(*) FROM posts WHERE workspace_id=%(w)s AND external_id IS NOT NULL AND v IS NOT NULL) AS scored_videos,
             (SELECT count(*) FROM posts WHERE workspace_id=%(w)s AND external_id IS NULL) AS board_cards,
             (SELECT count(*) FROM posts WHERE workspace_id=%(w)s AND external_id IS NULL AND status IN ('scripted','planned')) AS scripted,
             (SELECT count(*) FROM posts WHERE workspace_id=%(w)s AND external_id IS NULL AND status='filmed') AS filmed,
             (SELECT count(*) FROM publish_jobs WHERE workspace_id=%(w)s AND status IN ('queued','scheduled')) AS scheduled,
             (SELECT role FROM memberships WHERE workspace_id=%(w)s AND user_id=%(u)s) AS role""",
        {"w": ws, "u": user_id}) or {}
    out = {k: (int(v) if isinstance(v, (int, float)) else v) for k, v in r.items()}
    try:
        from . import billing
        out["trial_or_plan_blocked"] = bool(billing.access_blocked(ws))
        out["publishing_needs_plan"] = bool(billing.publishing_held(ws))
    except Exception:
        pass
    return out


def _clean(text: str, limit: int) -> str:
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text[:limit]


SYSTEM = """You are the HookTrack Guide, a friendly helper inside the HookTrack app. You talk to everyday people who are not technical.

WHO YOU ARE TALKING TO
Someone who opened this chat only after looking around the app and still not understanding something. Treat it that way:
- Do NOT greet with a tour or repeat what is already printed on their screen.
- Use "WHERE THEY ARE" and "PAGES THEY LOOKED AT" to work out what is most likely confusing them, and answer that first. If their question is clear, just answer it.
- If you truly cannot tell what they need, ask ONE short question, offering two or three likely options.
- Notice real blockers in "THEIR WORKSPACE" (for example no accounts yet, nothing synced, no video attached, a plan needed) and say so plainly when it explains their problem.

HOW TO TALK
- Plain everyday words, like explaining to a friend who has never used the app. No jargon. If you must use a HookTrack word (hook, normal, Scale), explain it in half a sentence the first time.
- Short. Usually under 110 words. One idea at a time. Put steps in a numbered list, and name the exact page and button, like: Open **Accounts**, then press **Sync all**.
- Warm and calm, never blaming. If something is genuinely confusing in the app, say "that part is easy to miss".
- End with one small next step or one helpful follow-up question when it fits. No filler.
- To send someone to a page, write a link like [Open Hooks](#/hooks). Use only these pages: #/dashboard #/accounts #/views #/today #/hooks #/pipeline #/scheduled #/clients #/inspiration #/settings #/billing.
- Plain text with light formatting only: **bold** for button and page names, numbered lists, and the links above. No tables, no headings, no code.

TRUTHFULNESS
- Answer only from THE GUIDE below, the screen text, and the workspace facts. Never invent a button, setting, price or feature.
- If the guide does not cover it, say you are not sure, give your best pointer to where to look, and suggest contacting support. Never guess numbers about their account that you were not given.
- You cannot click, post, or change anything: you only explain and point. Never ask for passwords, card numbers or API keys, and tell people not to share them.
- Stay on HookTrack. For anything else, kindly say you can only help with HookTrack.

THE GUIDE
"""


def build_prompt(ws: int, user_id: int, page: dict) -> str:
    facts = workspace_facts(ws, user_id)
    route = _clean(page.get("route"), 80) or "#/dashboard"
    title = _clean(page.get("title"), 80)
    trail = []
    for t in (page.get("trail") or [])[-MAX_TRAIL:]:
        if isinstance(t, dict):
            name = _clean(t.get("page"), 40)
            secs = t.get("seconds")
            if name:
                trail.append(f"{name}" + (f" (about {int(secs)}s)" if isinstance(secs, (int, float)) and secs >= 5 else ""))
    screen = _clean(page.get("screen"), MAX_SCREEN_CHARS)
    problem = _clean(page.get("problem"), 300)
    extra = [
        f"WHERE THEY ARE NOW: {title or route} ({route})",
        "PAGES THEY LOOKED AT THIS SESSION (oldest first): " + (" > ".join(trail) if trail else "just this page"),
        f"THEIR WORKSPACE: {facts}",
    ]
    if problem:
        extra.append(f"A MESSAGE OR ERROR THEY RECENTLY SAW: {problem}")
    if screen:
        extra.append("TEXT CURRENTLY ON THEIR SCREEN (may be partial):\n" + screen)
    return SYSTEM + knowledge() + "\n\n---\nCONTEXT FOR THIS CHAT\n" + "\n".join(extra)


def clean_messages(messages: list) -> list:
    """Only alternating user/assistant text turns, trimmed, ending on a user turn."""
    out = []
    for m in (messages or [])[-MAX_TURNS:]:
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            continue
        text = _clean(m.get("content"), MAX_MESSAGE_CHARS)
        if not text:
            continue
        if out and out[-1]["role"] == m["role"]:
            out[-1]["content"] += "\n" + text
        else:
            out.append({"role": m["role"], "content": text})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def reply(cfg, ws: int, user_id: int, messages: list, page: dict) -> str:
    msgs = clean_messages(messages)
    if not msgs or msgs[-1]["role"] != "user":
        raise ValueError("Ask a question first")
    return ai.chat(cfg, build_prompt(ws, user_id, page), msgs, max_tokens=700)
