"""The winning video as a baseline to imitate.

A post that measured as a winner (or that the owner chose to Scale) can be turned into a *blueprint*: what is
said in the first seconds, how the video is built, the sound it used, and what it achieved. New posts are then
written as clones that keep the proven mechanics and change the specifics. Each clone remembers its parent
(`posts.cloned_from`), so the scorecard can answer the honest question: do clones of winners also do well?

Nothing here predicts anything. Decisions still come from measured results; this is what you do *after* one.
Meant for the owner's own videos: for anyone else's, use the patterns, not their script.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import ai, db, logic, sociavault
from .auth import Ctx, current

router = APIRouter(prefix="/api", tags=["baseline"])

HOOK_WINDOW_S = 3.5            # "the hook" = what is said in the first few seconds
MAX_BEATS = 6
CTA_RE = re.compile(r"\b(link in (my )?bio|follow|subscribe|comment|save (this|it)|share|shop|use (code|my code)|"
                    r"dm me|check out|click|tap|order|sign up|join|get yours|swipe up|grab)\b", re.I)
QUESTION_RE = re.compile(r"\?")
NUMBER_RE = re.compile(r"\b\d[\d,.]*\b|\b(one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)


# ---------- transcript parsing ----------

_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_CUE = re.compile(rf"{_TIME}\s*-->\s*{_TIME}")


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")[:3]) / 1000.0


def _clean(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)                      # <c>, <v Speaker>, <00:00:01.000>
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_transcript(raw: Any) -> list[dict]:
    """Normalise whatever the provider returned into cues: [{start, end, text}] (start/end None when untimed).
    Accepts WebVTT/SRT text, plain text, or a list of segment objects."""
    cues: list[dict] = []
    if isinstance(raw, dict):
        for k in ("segments", "cues", "captions", "transcript", "transcripts", "items"):
            if raw.get(k):
                return parse_transcript(raw[k])
        vals = list(raw.values())
        if vals and all(isinstance(v, dict) for v in vals):          # {"0": {...}, "1": {...}}
            try:
                vals = [raw[k] for k in sorted(raw, key=lambda x: int(x))]
            except (TypeError, ValueError):
                pass
            return parse_transcript(vals)
        if isinstance(raw.get("text"), str):
            return parse_transcript(raw["text"])
        return []
    if isinstance(raw, list):
        for seg in raw:
            if isinstance(seg, str):
                t = _clean(seg)
                if t:
                    cues.append({"start": None, "end": None, "text": t})
                continue
            if not isinstance(seg, dict):
                continue
            t = _clean(str(seg.get("text") or seg.get("snippet") or seg.get("content") or ""))
            if not t:
                continue
            start = end = None
            try:
                if seg.get("startMs") is not None or seg.get("start_ms") is not None:
                    ms = seg.get("startMs") if seg.get("startMs") is not None else seg.get("start_ms")
                    ems = seg.get("endMs") if seg.get("endMs") is not None else seg.get("end_ms")
                    start = float(ms) / 1000.0
                    end = float(ems) / 1000.0 if ems is not None else None
                else:
                    s0 = seg.get("start") if seg.get("start") is not None else seg.get("offset")
                    dur = seg.get("duration") if seg.get("duration") is not None else seg.get("dur")
                    if s0 is not None:
                        start = float(s0) / (1000.0 if seg.get("offset") is not None and float(s0) > 1000 else 1.0)
                        end = start + float(dur) if dur is not None else None
            except (TypeError, ValueError):
                start = end = None
            cues.append({"start": start, "end": end, "text": t})
        return _dedupe(cues)
    text = str(raw or "").replace("\r\n", "\n")
    if not text.strip():
        return []
    if text.lstrip()[:1] in ("{", "["):                      # a structured response that was stored as JSON
        try:
            return parse_transcript(json.loads(text))
        except ValueError:
            pass
    if "-->" in text:
        blocks = re.split(r"\n\s*\n", text)
        for b in blocks:
            lines = [ln for ln in b.split("\n") if ln.strip()]
            for i, ln in enumerate(lines):
                m = _CUE.search(ln)
                if m:
                    g = m.groups()
                    start = _secs(g[0], g[1], g[2], g[3])
                    end = _secs(g[4], g[5], g[6], g[7])
                    body = _clean(" ".join(lines[i + 1:]))
                    if body:
                        cues.append({"start": start, "end": end, "text": body})
                    break
        return _dedupe(cues)
    plain = _clean(re.sub(r"^WEBVTT.*$", "", text, flags=re.M))
    return [{"start": None, "end": None, "text": plain}] if plain else []


def _dedupe(cues: list[dict]) -> list[dict]:
    """Auto-captions repeat the previous line in the next cue: keep each line once."""
    out: list[dict] = []
    for c in cues:
        if out and (c["text"] == out[-1]["text"] or out[-1]["text"].endswith(c["text"])):
            if c["end"] is not None:
                out[-1]["end"] = c["end"]
            continue
        adjacent = (c["start"] is None or out and out[-1]["end"] is None
                    or (out and c["start"] <= (out[-1]["end"] or 0) + 0.35))
        if out and c["text"].startswith(out[-1]["text"]) and adjacent:
            out[-1]["text"] = c["text"]                     # rolling caption grew: replace, do not stack
            out[-1]["end"] = c["end"] if c["end"] is not None else out[-1]["end"]
            continue
        out.append(dict(c))
    return out


def full_text(cues: list[dict]) -> str:
    return " ".join(c["text"] for c in cues).strip()


def spoken_hook(cues: list[dict], window: float = HOOK_WINDOW_S, max_chars: int = 220) -> str:
    """What is said in the first seconds. Untimed transcripts fall back to the first sentence or two."""
    if not cues:
        return ""
    timed = [c for c in cues if c["start"] is not None]
    if timed:
        first = [c for c in timed if c["start"] < window] or timed[:1]
        text = " ".join(c["text"] for c in first)
    else:
        text = cues[0]["text"]
    # a hook is a line, not a paragraph: stop at the sentence that reaches a speakable length
    out, words = [], 0
    for sent in re.split(r"(?<=[.!?])\s+", text):
        out.append(sent)
        words += len(sent.split())
        if words >= 8:
            break
    return " ".join(out)[:max_chars].strip()


# ---------- deterministic blueprint ----------

def _beats(cues: list[dict], duration_s: Optional[float]) -> list[dict]:
    timed = [c for c in cues if c["start"] is not None]
    if not timed:
        text = full_text(cues)
        sents = [s for s in re.split(r"(?<=[.!?])\s+", text) if s]
        return [{"at": None, "says": s[:240]} for s in sents[:MAX_BEATS]]
    total = duration_s or max((c["end"] or c["start"]) for c in timed) or 1.0
    n = min(MAX_BEATS, max(2, int(total // 6) + 1))
    size = total / n
    beats = []
    for i in range(n):
        lo, hi = i * size, (i + 1) * size
        said = " ".join(c["text"] for c in timed if lo <= c["start"] < hi).strip()
        if said:
            beats.append({"at": round(lo, 1), "says": said[:240]})
    return beats


def signals(cues: list[dict], duration_s: Optional[float]) -> dict:
    text = full_text(cues)
    hook = spoken_hook(cues)
    words = len(text.split())
    dur = duration_s or (max((c["end"] or c["start"] or 0) for c in cues) if cues else 0) or None
    ctas = [m.group(0) for m in CTA_RE.finditer(text)]
    return {
        "opens_with_question": bool(QUESTION_RE.search(hook)),
        "has_number_in_hook": bool(NUMBER_RE.search(hook)),
        "hook_words": len(hook.split()),
        "total_words": words,
        "words_per_second": round(words / dur, 2) if dur else None,
        "speaks_to_viewer": len(re.findall(r"\b(you|your|you're|you've)\b", hook, re.I)) > 0,
        "call_to_action": ctas[-1] if ctas else "",
        "has_speech": words >= 3,
    }


def build_blueprint(post: dict, cues: list[dict], perf: Optional[dict], ai_part: Optional[dict] = None) -> dict:
    sig = signals(cues, post.get("duration_s"))
    bp: dict[str, Any] = {
        "version": 1,
        "spoken_hook": spoken_hook(cues),
        "spoken_at": next((round(c["start"], 1) for c in cues if c["start"] is not None), None),
        "beats": _beats(cues, post.get("duration_s")),
        "signals": sig,
        "duration_s": post.get("duration_s"),
        "sound": post.get("sound") or "",
        "caption": (post.get("caption") or "")[:600],
        "results": {},
        "ai": bool(ai_part),
    }
    if perf:
        bp["results"] = {k: perf.get(k) for k in ("label", "multiple", "intent_per_1k", "buy_comments", "ask_comments")
                         if perf.get(k) is not None}
    if ai_part:
        bp.update({k: ai_part[k] for k in ("format", "tone", "structure", "keep", "vary", "beat_roles") if ai_part.get(k)})
    return bp


# ---------- fetching ----------

def _post_row(ws: int, pid: int) -> dict:
    row = db.one(
        """SELECT p.*, a.handle, a.platform FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.id=%s AND p.workspace_id=%s""", (pid, ws))
    if row is None:
        raise HTTPException(404, "Post not found")
    return row


def _public_url(row: dict) -> str:
    return sociavault.post_url_for(sociavault.canonical_platform(row.get("platform") or "").lower(),
                                   row.get("handle") or "", row.get("external_id") or "", row.get("url") or "")


_TT_ID = re.compile(r"/video/(\d{6,25})(?:\D|$)")
_YT_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])")
_IG_CODE = re.compile(r"instagram\.com/(?:reels?|p|tv)/([A-Za-z0-9_-]{5,24})")


def _on_host(url: str, *domains: str) -> bool:
    from urllib.parse import urlparse
    try:
        h = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(h == d or h.endswith("." + d) for d in domains)


def embed_for(platform: str, url: str, external_id: str = "") -> Optional[dict]:
    """How to show the original video in the page, or None. The address is rebuilt from a validated ID on the
    platform's own embed route, never taken from the stored URL as-is, so only those three hosts can be framed.
    A link that is not actually on the platform's own domain is ignored."""
    p = (sociavault.canonical_platform(platform or "") or "").lower()
    url = url or ""
    domains = {"tiktok": ("tiktok.com",), "youtube": ("youtube.com", "youtu.be", "youtube-nocookie.com"),
               "instagram": ("instagram.com",)}
    for key, doms in domains.items():
        if p.startswith(key) and url and not _on_host(url, *doms):
            url = ""                                         # off-platform link: only a plain platform id may be used
    if p.startswith("tiktok"):
        m = _TT_ID.search(url)
        vid = m.group(1) if m else (external_id if re.fullmatch(r"\d{6,25}", external_id or "") else "")
        if vid:
            return {"provider": "tiktok", "label": "TikTok", "src": f"https://www.tiktok.com/embed/v2/{vid}",
                    "height": 600, "max_width": 340}
    elif p.startswith("youtube"):
        m = _YT_ID.search(url)
        vid = m.group(1) if m else (external_id if re.fullmatch(r"[A-Za-z0-9_-]{11}", external_id or "") else "")
        if vid:
            # the privacy-enhanced domain: it sets no cookies until the viewer plays the video
            return {"provider": "youtube", "label": "YouTube", "src": f"https://www.youtube-nocookie.com/embed/{vid}",
                    "aspect": "16 / 9"}
    elif p.startswith("instagram"):
        m = _IG_CODE.search(url)
        if m:
            return {"provider": "instagram", "label": "Instagram",
                    "src": f"https://www.instagram.com/p/{m.group(1)}/embed/", "height": 620, "max_width": 400}
    return None


def already_read(row: dict) -> bool:
    """True once this video has been read, even if nothing was found (silent / music-only), or was marked silent.
    An empty result is still a result: asking again would spend another credit for the same nothing."""
    return bool(row.get("transcript") or row.get("transcript_at") or row.get("transcript_skipped"))


def ensure_transcript(ws: int, pid: int, refresh: bool = False, skip: bool = False) -> dict:
    """Fetch and store the transcript once (about one SociaVault credit).
    skip=True marks the video as having no speech and never calls the provider.
    refresh=True forces a new read (spends a credit again)."""
    row = _post_row(ws, pid)
    if skip and not refresh:
        db.execute("UPDATE posts SET transcript_skipped=TRUE WHERE id=%s AND workspace_id=%s", (pid, ws))
        return _post_row(ws, pid)
    if already_read(row) and not refresh:
        return row
    platform = sociavault.canonical_platform(row.get("platform") or "").lower()
    if platform not in ("tiktok", "instagram", "youtube"):
        raise HTTPException(400, "Transcripts are available for TikTok, Instagram and YouTube posts.")
    url = _public_url(row)
    if not url:
        raise HTTPException(400, "This post has no public link to read a transcript from. Sync it from the platform first.")
    key = sociavault.resolve_key(ws)
    if not key:
        raise HTTPException(400, "Reading videos is not set up on this server yet (no SociaVault connection).")
    try:
        sociavault.credit_gate(ws, 1)
        raw = sociavault.fetch_transcript(platform, url, key)
        sociavault.record_credits(ws, "read", 1)
    except sociavault.SociaVaultError as e:
        raise HTTPException(e.status if e.status in (401, 403, 404) else 400, str(e))
    cues = parse_transcript(raw)
    raw_store = raw if isinstance(raw, str) else json.dumps(raw)[:60000]
    # transcript_at is set even when nothing was said: that is how "read, found nothing" differs from "never read"
    db.execute(
        """UPDATE posts SET transcript=%s, transcript_at=now(), spoken_hook=%s, transcript_skipped=FALSE
           WHERE id=%s AND workspace_id=%s""",
        ((raw_store or "")[:60000], spoken_hook(cues), pid, ws))
    return _post_row(ws, pid)


def make_blueprint(ws: int, pid: int, refresh: bool = False, skip: bool = False) -> dict:
    row = ensure_transcript(ws, pid, refresh=refresh, skip=skip)
    cues = parse_transcript(row["transcript"])
    perf = logic.performance(ws).get(pid)
    ai_part = None
    notes = (row.get("video_notes") or "").strip()
    # A silent video with no note gives the model nothing to read: do not pay for a model call that cannot add anything
    if cues or len(notes) >= 10:
        try:
            cfg = ai.resolve(ws)
            if cfg is not None and getattr(cfg, "available", True):
                ai_part = ai.structure_blueprint(cfg, row, full_text(cues), spoken_hook(cues), perf, notes)
        except Exception:                                # AI is a bonus here: the deterministic blueprint stands alone
            ai_part = None
    bp = build_blueprint(row, cues, perf, ai_part)
    bp["transcript_state"] = ("skipped" if row.get("transcript_skipped") and not row.get("transcript_at")
                              else ("none" if row.get("transcript_at") and not cues else ("read" if cues else "unread")))
    db.execute("UPDATE posts SET blueprint=%s, spoken_hook=%s WHERE id=%s AND workspace_id=%s",
               (db.Json(bp), bp["spoken_hook"], pid, ws))
    return bp


# ---------- matching videos ----------

def clones_of(ws: int, pid: int) -> list[dict]:
    perf = logic.performance(ws)
    rows = db.query(
        """SELECT p.id, p.hook, p.status, p.views, p.variant_label, p.post_date,
                  (p.video_file <> '') AS has_file, (p.video_url <> '') AS has_url, p.video_file_name,
                  (SELECT j.status FROM video_jobs j WHERE j.post_id = p.id ORDER BY j.id DESC LIMIT 1) AS job_status
             FROM posts p
            WHERE p.workspace_id=%s AND p.cloned_from=%s ORDER BY p.id""", (ws, pid))
    out = []
    for r in rows:
        pf = perf.get(r["id"]) or {}
        out.append({**r, "label": pf.get("label"), "multiple": pf.get("multiple"),
                    "intent_per_1k": pf.get("intent_per_1k")})
    return out


def scorecard(ws: int) -> dict:
    """How clones of winners did against their own accounts' normal. Only measured clones count."""
    perf = logic.performance(ws)
    kids = db.query(
        """SELECT c.id, c.cloned_from, c.hook, c.variant_label, p.hook AS parent_hook
           FROM posts c JOIN posts p ON p.id = c.cloned_from
           WHERE c.workspace_id=%s AND c.cloned_from IS NOT NULL""", (ws,))
    measured, labels = [], {"WINNER": 0, "AVERAGE": 0, "DUD": 0}
    per_parent: dict[int, dict] = {}
    for k in kids:
        pf = perf.get(k["id"]) or {}
        par = perf.get(k["cloned_from"]) or {}
        d = per_parent.setdefault(k["cloned_from"], {"parent_id": k["cloned_from"], "parent_hook": k["parent_hook"],
                                                      "parent_multiple": par.get("multiple"), "clones": 0, "measured": 0,
                                                      "winners": 0, "best_multiple": None})
        d["clones"] += 1
        if pf.get("label") in labels and pf.get("multiple") is not None:
            d["measured"] += 1
            labels[pf["label"]] += 1
            measured.append(pf["multiple"])
            if pf["label"] == "WINNER":
                d["winners"] += 1
            if d["best_multiple"] is None or pf["multiple"] > d["best_multiple"]:
                d["best_multiple"] = pf["multiple"]
    n = len(measured)
    return {
        "clones": len(kids), "measured": n, "winners": labels["WINNER"], "average": labels["AVERAGE"],
        "duds": labels["DUD"],
        "win_rate": round(labels["WINNER"] / n, 2) if n else None,
        "held_rate": round(sum(1 for m in measured if m >= 1.0) / n, 2) if n else None,   # at least the account's normal
        "median_multiple": round(sorted(measured)[n // 2], 2) if n else None,
        "enough": n >= 5,
        "parents": sorted(per_parent.values(), key=lambda d: -(d["best_multiple"] or 0)),
    }


# ---------- routes ----------

def _parent_view(ws: int, parent_id: Optional[int]) -> Optional[dict]:
    if not parent_id:
        return None
    pr = db.one(
        """SELECT p.id, p.hook, p.external_id, p.url, a.handle, a.platform FROM posts p
           LEFT JOIN accounts a ON a.id = p.account_id WHERE p.id=%s AND p.workspace_id=%s""", (parent_id, ws))
    if pr is None:
        return None
    src = _public_url(pr)
    return {"id": pr["id"], "hook": pr["hook"], "source_url": src,
            "embed": embed_for(pr.get("platform") or "", src, pr.get("external_id") or "")}


def _brief(ws: int, row: dict) -> Optional[dict]:
    """What a card made from a hook should be: the hook's results, how it is built, and what to do with it."""
    origin = row.get("cloned_from") or row.get("followup_of")
    if not origin:
        return None
    pr = db.one(
        """SELECT p.id, p.hook, p.body, p.caption, p.views, p.external_id, p.url, p.spoken_hook, p.sound, p.blueprint,
                  a.handle, a.platform FROM posts p LEFT JOIN accounts a ON a.id = p.account_id
           WHERE p.id=%s AND p.workspace_id=%s""", (origin, ws))
    if pr is None:
        return None
    bp = pr.get("blueprint")
    if isinstance(bp, str):
        bp = json.loads(bp)
    bp = bp or {}
    pf = logic.performance(ws).get(origin) or {}
    src = _public_url(pr)
    return {
        # a Tweak card that has been moved past Idea is linked as a matching video, but is still a tweak in what it asks for
        "kind": "scale" if (row.get("cloned_from") and row.get("followup_decision") != "MUTATE") else "tweak",
        "promoted": bool(row.get("cloned_from")) and row.get("followup_decision") == "MUTATE",
        "hook_id": pr["id"], "hook": pr["hook"] or "", "caption": pr["caption"] or "", "views": pr["views"],
        "multiple": pf.get("multiple"), "label": pf.get("label"),
        "spoken_hook": pr["spoken_hook"] or bp.get("spoken_hook") or "", "sound": pr["sound"] or "",
        "structure": bp.get("structure") or "", "format": bp.get("format") or "", "duration_s": bp.get("duration_s"),
        "beats": [{"role": b.get("role"), "what": b.get("what_happens")} for b in (bp.get("beat_roles") or [])][:8],
        "read": bool(bp), "source_url": src,
        "embed": embed_for(pr.get("platform") or "", src, pr.get("external_id") or ""),
    }


def _inspiration(ws: int, pid: int, row: dict) -> list:
    if row.get("external_id") is not None:          # a synced hook is a result, not a card with a brief
        return []
    from . import inspire
    return inspire.for_post(ws, pid)


def _view(ws: int, pid: int) -> dict:
    row = _post_row(ws, pid)
    bp = row.get("blueprint")
    if isinstance(bp, str):
        bp = json.loads(bp)
    perf = logic.performance(ws).get(pid) or {}
    return {
        "post_id": pid, "has_transcript": bool(row["transcript"]), "transcript_at": row["transcript_at"],
        "read": bool(row["transcript_at"]), "skipped": bool(row.get("transcript_skipped")),
        "no_speech": bool(row["transcript_at"]) and not (row["transcript"] or "").strip()
                     or bool(row.get("transcript_skipped")) and not row["transcript"],
        "spoken_hook": row["spoken_hook"] or ((bp or {}).get("spoken_hook") or ""), "sound": row["sound"],
        "video_notes": row["video_notes"],
        "blueprint": bp, "is_winner": perf.get("label") == "WINNER", "label": perf.get("label"),
        "multiple": perf.get("multiple"), "has_url": bool(_public_url(row)), "source_url": _public_url(row),
        "embed": embed_for(row.get("platform") or "", _public_url(row), row.get("external_id") or ""),
        "cloned_from": row["cloned_from"], "clones": clones_of(ws, pid),
        "inspiration": _inspiration(ws, pid, row),
        "parent": _parent_view(ws, row["cloned_from"]),
        "brief": _brief(ws, row),
    }


@router.get("/posts/{pid}/baseline")
def baseline_get(pid: int, ctx: Ctx = Depends(current)):
    return _view(ctx.workspace_id, pid)


@router.post("/posts/{pid}/baseline")
def baseline_build(pid: int, refresh: bool = False, skip_transcript: bool = False, ctx: Ctx = Depends(current)):
    """Read the transcript (about 1 SociaVault credit, once) and build the blueprint.
    skip_transcript=true builds it without any paid call, for a video you know has no speech."""
    had = already_read(_post_row(ctx.workspace_id, pid))
    make_blueprint(ctx.workspace_id, pid, refresh=refresh, skip=skip_transcript)
    spent = 0 if (skip_transcript and not refresh) or (had and not refresh) else 1
    return {**_view(ctx.workspace_id, pid), "credits_est": spent}


class NotesIn(BaseModel):
    notes: str = Field("", max_length=1500)


@router.put("/posts/{pid}/video-notes")
def video_notes(pid: int, b: NotesIn, ctx: Ctx = Depends(current)):
    _post_row(ctx.workspace_id, pid)
    db.execute("UPDATE posts SET video_notes=%s WHERE id=%s AND workspace_id=%s", (b.notes.strip(), pid, ctx.workspace_id))
    return {"ok": True, "video_notes": b.notes.strip()}


@router.get("/posts/{pid}/clones")
def clones_list(pid: int, ctx: Ctx = Depends(current)):
    _post_row(ctx.workspace_id, pid)
    return {"clones": clones_of(ctx.workspace_id, pid)}


@router.get("/clones/scorecard")
def clones_scorecard(ctx: Ctx = Depends(current)):
    return scorecard(ctx.workspace_id)
