"""Videos the owner uploads from their own machine, as an alternative to provider-generated ones.

* Stored per workspace under uploads/<ws>/videos/<post>/ with a random name, streamed to disk with a size cap, and
  accepted only if the first bytes really are an MP4/MOV or WebM container (a renamed file is refused).
* The owner previews it through an authenticated route (cookie + workspace check), with range requests so the
  player can seek.
* A publishing provider has no cookie, so it fetches the file from a short-lived, signed, per-post link.
  That only works when the server has a public address (HOOKTRACK_PUBLIC_URL); otherwise publishing says so
  plainly instead of sending the provider a link it cannot open.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from . import crypto, db
from .auth import Ctx, current

router = APIRouter(tags=["media"])

UPLOAD_ROOT = Path(__file__).parent.parent / "uploads"
CHUNK = 1024 * 1024
SIGNED_TTL_S = 48 * 3600
TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}


def max_bytes() -> int:
    try:
        mb = int(os.environ.get("HOOKTRACK_MAX_VIDEO_MB", "300"))
    except ValueError:
        mb = 300
    return max(1, mb) * 1024 * 1024


def sniff(head: bytes) -> Optional[str]:
    """The container type from the first bytes, or None when it is not a video we accept."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        return "mov" if brand == b"qt  " else "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":                       # EBML: WebM / Matroska
        return "webm"
    return None


def _post(ws: int, pid: int) -> dict:
    row = db.one("SELECT id, video_file, video_file_name, video_file_size, video_file_type, video_url "
                 "FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    if row is None:
        raise HTTPException(404, "Post not found")
    return row


def _unlocked(ws: int, pid: int) -> None:
    """A Filmed card's video is fixed: to change it, move the card back to Scripted first."""
    r = db.one("SELECT status FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    if r and r["status"] == "filmed":
        raise HTTPException(409, "Uploads are disabled on a Filmed card. Move it back to Scripted to change its video.")


def _abs(rel: str) -> Optional[Path]:
    """The file path, only if it really lives under uploads/ (a stored path is never trusted blindly)."""
    if not rel:
        return None
    p = (UPLOAD_ROOT / rel).resolve()
    try:
        p.relative_to(UPLOAD_ROOT.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def remove_file(ws: int, pid: int) -> None:
    """Delete the stored file and forget it on the post."""
    row = db.one("SELECT video_file FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    if row and row["video_file"]:
        f = _abs(row["video_file"])
        if f:
            try:
                f.unlink()
            except OSError:
                pass
    db.execute("UPDATE posts SET video_file='', video_file_name='', video_file_size=0, video_file_type='', "
               "video_file_at=NULL WHERE id=%s AND workspace_id=%s", (pid, ws))


def purge_post_files(ws: int, pid: int) -> None:
    shutil.rmtree(UPLOAD_ROOT / str(ws) / "videos" / str(pid), ignore_errors=True)


# ---------- signed links for providers ----------

def _sign(ws: int, pid: int, rel: str, exp: int) -> str:
    key = hashlib.sha256(("media:" + crypto._load_secret()).encode()).digest()
    return hmac.new(key, f"{ws}:{pid}:{rel}:{exp}".encode(), hashlib.sha256).hexdigest()[:40]


def public_base() -> str:
    return (os.environ.get("HOOKTRACK_PUBLIC_URL") or "").strip().rstrip("/")


def _is_private_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".local") or host.startswith("192.168.") \
        or host.startswith("10.") or host.startswith("172.16.")


def signed_url(ws: int, pid: int, ttl: int = SIGNED_TTL_S) -> str:
    """A link a provider can use to download the uploaded video. Raises ValueError with a reason when it cannot."""
    row = db.one("SELECT video_file FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    if not row or not row["video_file"] or not _abs(row["video_file"]):
        raise ValueError("This post has no uploaded video file.")
    base = public_base()
    if not base:
        raise ValueError("The video is stored on this server, and publishing providers need a public address to "
                         "download it from. Set HOOKTRACK_PUBLIC_URL to this site's public address, or use a "
                         "video URL instead.")
    if _is_private_host(base):
        raise ValueError(f"HOOKTRACK_PUBLIC_URL ({base}) is not reachable from the internet, so a publishing "
                         f"provider could not download the video. Use a public address, or a video URL instead.")
    exp = int(time.time()) + ttl
    return f"{base}/media/v/{ws}/{pid}/{exp}/{_sign(ws, pid, row['video_file'], exp)}"


# ---------- routes ----------

async def _store_upload(ws: int, pid: int, file: UploadFile, request: Request) -> dict:
    """Validate, stream to disk and attach an uploaded video to post `pid` (replacing any earlier file)."""
    limit = max_bytes()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 4096:
        raise HTTPException(413, f"That file is too large. The limit is {limit // (1024 * 1024)} MB.")
    folder = UPLOAD_ROOT / str(ws) / "videos" / str(pid)
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".incoming-{secrets.token_hex(6)}"
    size, kind = 0, None
    try:
        with open(tmp, "wb") as out:
            while True:
                chunk = await file.read(CHUNK)
                if not chunk:
                    break
                if kind is None:
                    kind = sniff(chunk[:16])
                    if kind is None:
                        raise HTTPException(415, "That is not a supported video. Upload an MP4, MOV or WebM file.")
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"That file is too large. The limit is {limit // (1024 * 1024)} MB.")
                out.write(chunk)
        if size == 0 or kind is None:
            raise HTTPException(400, "The file is empty.")
        final = folder / f"{secrets.token_urlsafe(18)}.{kind}"
        os.replace(tmp, final)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    # one uploaded video per post: the new one replaces the old
    old = db.one("SELECT video_file FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))["video_file"]
    name = (file.filename or "video").replace("\\", "/").rsplit("/", 1)[-1][:120]
    db.execute(
        """UPDATE posts SET video_file=%s, video_file_name=%s, video_file_size=%s, video_file_type=%s,
             video_file_at=now(), video_url=''
           WHERE id=%s AND workspace_id=%s""",
        (f"{ws}/videos/{pid}/{final.name}", name, size, TYPES[kind], pid, ws))
    if old:
        f = _abs(old)
        if f and f != final:
            try:
                f.unlink()
            except OSError:
                pass
    return {"ok": True, "name": name, "size": size, "type": TYPES[kind]}


@router.post("/api/posts/{pid}/video/upload")
async def upload_video(pid: int, request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    """Attach a video to THIS post (the editor of a matching video uses this)."""
    _post(ctx.workspace_id, pid)
    _unlocked(ctx.workspace_id, pid)
    return await _store_upload(ctx.workspace_id, pid, file, request)


@router.post("/api/posts/{pid}/matching-video")
async def matching_video(pid: int, request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    """Dropped on a winning hook: the video is a NEW video that matches it. It becomes its own post, linked to the
    winner, starting from the winner's hook and caption so it can be edited, scheduled and measured like any other."""
    ws = ctx.workspace_id
    parent = db.one("SELECT id, account_id, hook, body, caption, ai_prompt FROM posts WHERE id=%s AND workspace_id=%s",
                    (pid, ws))
    if parent is None:
        raise HTTPException(404, "Post not found")
    from . import logic
    n = db.one("SELECT COUNT(*) AS n FROM posts WHERE workspace_id=%s AND cloned_from=%s", (ws, pid))["n"] + 1
    row = db.one(
        """INSERT INTO posts (workspace_id, account_id, post_date, status, ai_prompt, hook, body, caption,
                              views, comments, intent, score_by, parent_id, cloned_from, variant_label)
           VALUES (%s,%s,%s,'idea',%s,%s,%s,%s,0,0,false,'',%s,%s,%s) RETURNING id""",
        (ws, parent["account_id"], logic.today(ws), parent["ai_prompt"] or "", parent["hook"] or "",
         parent["body"] or "", parent["caption"] or "", pid, pid, f"C{n}"))
    try:
        out = await _store_upload(ws, row["id"], file, request)
    except BaseException:                                  # a refused file must not leave an empty post behind
        db.execute("DELETE FROM posts WHERE id=%s AND workspace_id=%s", (row["id"], ws))
        purge_post_files(ws, row["id"])
        raise
    return {"post_id": row["id"], "label": f"C{n}", "parent_id": pid, **out}


@router.get("/api/posts/{pid}/video/file")
def video_file(pid: int, ctx: Ctx = Depends(current)):
    """The owner's preview. Cookie + workspace checked; range requests supported so the player can seek."""
    row = _post(ctx.workspace_id, pid)
    f = _abs(row["video_file"])
    if not f:
        raise HTTPException(404, "No uploaded video for this post")
    return FileResponse(f, media_type=row["video_file_type"] or "video/mp4",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/api/posts/{pid}/video/file")
def video_file_delete(pid: int, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    _unlocked(ctx.workspace_id, pid)
    remove_file(ctx.workspace_id, pid)
    return {"ok": True}


@router.get("/media/v/{ws}/{pid}/{exp}/{sig}")
def signed_media(ws: int, pid: int, exp: int, sig: str):
    """What a publishing provider downloads. No cookie: the signature is the credential, and it expires."""
    row = db.one("SELECT video_file, video_file_type FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    if (not row or not row["video_file"] or exp < int(time.time())
            or not hmac.compare_digest(sig, _sign(ws, pid, row["video_file"], exp))):
        raise HTTPException(404, "Not found")
    f = _abs(row["video_file"])
    if not f:
        raise HTTPException(404, "Not found")
    return FileResponse(f, media_type=row["video_file_type"] or "video/mp4",
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})
