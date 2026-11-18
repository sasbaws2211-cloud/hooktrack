"""Video and image files the owner uploads from their own machine, as an alternative to provider-generated ones.

* A video is stored per workspace under uploads/<ws>/videos/<post>/ with a random name, streamed to disk with a
  size cap, and accepted only if the first bytes really are an MP4/MOV or WebM container (a renamed file is
  refused). One per post; a new upload replaces the old one (posts.video_file et al).
* Images (a photo post, or several for a carousel) live the same way under uploads/<ws>/images/<post>/, but as
  an ORDERED LIST in the post_media table - a photo is one image, a carousel is several, in the order they'll
  post. Accepted only if the first bytes really are a JPEG, PNG or WebP.
* The owner previews either through an authenticated route (cookie + workspace check); video previews support
  range requests so the player can seek.
* A publishing provider has no cookie, so it fetches files from short-lived, signed, per-file links. That only
  works when the server has a public address (HOOKTRACK_PUBLIC_URL); otherwise publishing says so plainly
  instead of sending the provider a link it cannot open.
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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import crypto, db
from .auth import Ctx, current

router = APIRouter(tags=["media"])

UPLOAD_ROOT = Path(__file__).parent.parent / "uploads"
CHUNK = 1024 * 1024
SIGNED_TTL_S = 48 * 3600
TYPES = {"mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm"}
IMAGE_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
MAX_IMAGES = 20   # Instagram's own documented carousel ceiling; the highest of any platform we publish to


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


def max_image_bytes() -> int:
    try:
        mb = int(os.environ.get("HOOKTRACK_MAX_IMAGE_MB", "20"))
    except ValueError:
        mb = 20
    return max(1, mb) * 1024 * 1024


def sniff_image(head: bytes) -> Optional[str]:
    """The image type from the first bytes, or None when it is not an image we accept."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    return None


AUDIO_TYPES = {"mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4", "ogg": "audio/ogg"}
STUDIO_FONTS = {"Roboto", "Arial", "Montserrat", "Poppins", "Inter"}


def sniff_audio(head: bytes) -> Optional[str]:
    """The audio type from the first bytes, or None when it is not an audio file we accept."""
    if head[:3] == b"ID3" or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xfa"):
        return "mp3"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if len(head) >= 12 and head[4:8] == b"ftyp":                  # m4a is an MP4 container, same as video
        return "m4a"
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
    shutil.rmtree(UPLOAD_ROOT / str(ws) / "images" / str(pid), ignore_errors=True)
    shutil.rmtree(UPLOAD_ROOT / str(ws) / "studio-covers" / str(pid), ignore_errors=True)


# ---------- images (photo post or carousel): an ordered list per post, in post_media ----------

def list_images(ws: int, pid: int) -> list[dict]:
    return db.query(
        "SELECT id, file_name, file_size, content_type, position FROM post_media "
        "WHERE post_id=%s AND workspace_id=%s ORDER BY position, id", (pid, ws))


def image_counts(ws: int, pids: list[int]) -> dict:
    """post_id -> image count, for a batch of posts (the posts list annotates each row with this)."""
    if not pids:
        return {}
    return {r["post_id"]: r["n"] for r in db.query(
        "SELECT post_id, COUNT(*) AS n FROM post_media WHERE workspace_id=%s AND post_id = ANY(%s) GROUP BY post_id",
        (ws, pids))}


def _image_row(ws: int, pid: int, media_id: int) -> dict:
    row = db.one("SELECT * FROM post_media WHERE id=%s AND post_id=%s AND workspace_id=%s", (media_id, pid, ws))
    if row is None:
        raise HTTPException(404, "Image not found")
    return row


def remove_image(ws: int, pid: int, media_id: int) -> None:
    row = _image_row(ws, pid, media_id)
    f = _abs(row["file_path"])
    if f:
        try:
            f.unlink()
        except OSError:
            pass
    db.execute("DELETE FROM post_media WHERE id=%s AND workspace_id=%s", (media_id, ws))


def reorder_images(ws: int, pid: int, ordered_ids: list[int]) -> None:
    """ordered_ids must be exactly this post's image ids, in the order they should post in."""
    have = {r["id"] for r in list_images(ws, pid)}
    if set(ordered_ids) != have:
        raise HTTPException(400, "The list must contain exactly this post's images, each once.")
    for i, mid in enumerate(ordered_ids):
        db.execute("UPDATE post_media SET position=%s WHERE id=%s AND workspace_id=%s", (i, mid, ws))


async def _store_image_upload(ws: int, pid: int, file: UploadFile, request: Request) -> dict:
    """Validate, stream to disk and append one image to this post's ordered list."""
    existing = list_images(ws, pid)
    if len(existing) >= MAX_IMAGES:
        raise HTTPException(400, f"A post can hold at most {MAX_IMAGES} images.")
    limit = max_image_bytes()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 4096:
        raise HTTPException(413, f"That image is too large. The limit is {limit // (1024 * 1024)} MB.")
    folder = UPLOAD_ROOT / str(ws) / "images" / str(pid)
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
                    kind = sniff_image(chunk[:16])
                    if kind is None:
                        raise HTTPException(415, "That is not a supported image. Upload a JPEG, PNG or WebP file.")
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"That image is too large. The limit is {limit // (1024 * 1024)} MB.")
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
    name = (file.filename or "image").replace("\\", "/").rsplit("/", 1)[-1][:120]
    next_pos = (max((r["position"] for r in existing), default=-1)) + 1
    row = db.one(
        """INSERT INTO post_media (workspace_id, post_id, position, file_path, file_name, file_size, content_type)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (ws, pid, next_pos, f"{ws}/images/{pid}/{final.name}", name, size, IMAGE_TYPES[kind]))
    return {"id": row["id"], "name": name, "size": size, "type": IMAGE_TYPES[kind], "position": next_pos}


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


def _public_base_or_raise(kind: str) -> str:
    base = public_base()
    if not base:
        raise ValueError(f"The {kind} is stored on this server, and publishing providers need a public address "
                         f"to download it from. Set HOOKTRACK_PUBLIC_URL to this site's public address, or use a "
                         f"{kind} URL instead.")
    if _is_private_host(base):
        raise ValueError(f"HOOKTRACK_PUBLIC_URL ({base}) is not reachable from the internet, so a publishing "
                         f"provider could not download the {kind}. Use a public address, or a {kind} URL instead.")
    return base


def signed_url(ws: int, pid: int, ttl: int = SIGNED_TTL_S) -> str:
    """A link a provider can use to download the uploaded video. Raises ValueError with a reason when it cannot."""
    row = db.one("SELECT video_file FROM posts WHERE id=%s AND workspace_id=%s", (pid, ws))
    if not row or not row["video_file"] or not _abs(row["video_file"]):
        raise ValueError("This post has no uploaded video file.")
    base = _public_base_or_raise("video")
    exp = int(time.time()) + ttl
    return f"{base}/media/v/{ws}/{pid}/{exp}/{_sign(ws, pid, row['video_file'], exp)}"


def signed_image_urls(ws: int, pid: int, ttl: int = SIGNED_TTL_S) -> list[str]:
    """Links a provider can use to download each of this post's images, in posting order. Raises ValueError with
    a reason when it cannot (no images, or no public address to serve them from)."""
    rows = db.query("SELECT id, file_path FROM post_media WHERE post_id=%s AND workspace_id=%s ORDER BY position, id",
                    (pid, ws))
    if not rows:
        raise ValueError("This post has no uploaded images.")
    base = _public_base_or_raise("image")
    exp = int(time.time()) + ttl
    return [f"{base}/media/i/{ws}/{pid}/{r['id']}/{exp}/{_sign(ws, pid, r['file_path'], exp)}" for r in rows]


def signed_image_url_by_id(ws: int, pid: int, media_id: int, ttl: int = SIGNED_TTL_S) -> str:
    row = db.one("SELECT file_path FROM post_media WHERE id=%s AND post_id=%s AND workspace_id=%s", (media_id, pid, ws))
    if not row:
        raise ValueError("Image not found.")
    base = _public_base_or_raise("image")
    exp = int(time.time()) + ttl
    return f"{base}/media/i/{ws}/{pid}/{media_id}/{exp}/{_sign(ws, pid, row['file_path'], exp)}"


def signed_audio_url_by_id(ws: int, pid: int, audio_id: int, ttl: int = SIGNED_TTL_S) -> str:
    row = db.one("SELECT file_path FROM post_audio WHERE id=%s AND post_id=%s AND workspace_id=%s", (audio_id, pid, ws))
    if not row:
        raise ValueError("Audio file not found.")
    base = _public_base_or_raise("audio")
    exp = int(time.time()) + ttl
    return f"{base}/media/a/{ws}/{pid}/{audio_id}/{exp}/{_sign(ws, pid, row['file_path'], exp)}"


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
@router.get("/api/posts/{pid}/video/file.{ext}")   # same response; some asset loaders (e.g. PixiJS, in the
                                                     # Studio editor) pick a parser from the URL's extension
                                                     # alone, never from the real Content-Type header
@router.head("/api/posts/{pid}/video/file")         # FastAPI doesn't register HEAD for a route just because
@router.head("/api/posts/{pid}/video/file.{ext}")   # GET is - Studio's own addClip() validates a new asset
                                                     # with a HEAD request first, before ever doing a GET
def video_file(pid: int, ctx: Ctx = Depends(current), ext: str = ""):
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


# ---------- image routes (a photo post, or a carousel's several photos) ----------

@router.get("/api/posts/{pid}/images")
def images_list(pid: int, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    return list_images(ctx.workspace_id, pid)


@router.post("/api/posts/{pid}/images/upload")
async def images_upload(pid: int, request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    """Append one image to this post's ordered list (a second upload adds a second carousel slide, not a
    replacement - unlike the single video slot)."""
    _post(ctx.workspace_id, pid)
    _unlocked(ctx.workspace_id, pid)
    return await _store_image_upload(ctx.workspace_id, pid, file, request)


@router.delete("/api/posts/{pid}/images/{media_id}")
def images_delete(pid: int, media_id: int, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    _unlocked(ctx.workspace_id, pid)
    remove_image(ctx.workspace_id, pid, media_id)
    return {"ok": True}


@router.patch("/api/posts/{pid}/images/reorder")
def images_reorder(pid: int, body: dict, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    _unlocked(ctx.workspace_id, pid)
    ids = body.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise HTTPException(400, "Body must be {\"ids\": [media_id, ...]} in the new order.")
    reorder_images(ctx.workspace_id, pid, ids)
    return {"ok": True}


@router.get("/api/posts/{pid}/images/{media_id}/file")
@router.get("/api/posts/{pid}/images/{media_id}/file.{ext}")   # same response; see the video route's note above
@router.head("/api/posts/{pid}/images/{media_id}/file")
@router.head("/api/posts/{pid}/images/{media_id}/file.{ext}")
def image_file(pid: int, media_id: int, ctx: Ctx = Depends(current), ext: str = ""):
    """The owner's preview. Cookie + workspace checked."""
    row = _image_row(ctx.workspace_id, pid, media_id)
    f = _abs(row["file_path"])
    if not f:
        raise HTTPException(404, "Image not found")
    return FileResponse(f, media_type=row["content_type"] or "image/jpeg",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


# ---------- Studio cover: one reusable, separately stored cover image per post ----------

def get_studio_cover(ws: int, pid: int) -> Optional[dict]:
    row = db.one(
        """SELECT file_name, file_size, content_type, title, logo_text, primary_color, accent_color,
                  font_family, updated_at, file_path
             FROM post_studio_covers WHERE post_id=%s AND workspace_id=%s""", (pid, ws))
    if row is None:
        return None
    available = _abs(row.pop("file_path")) is not None
    if hasattr(row.get("updated_at"), "isoformat"):
        row["updated_at"] = row["updated_at"].isoformat()
    row["available"] = available
    row["url"] = f"/api/posts/{pid}/studio-cover/file" if available else ""
    return row


@router.get("/api/posts/{pid}/studio-cover")
def studio_cover_get(pid: int, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    return get_studio_cover(ctx.workspace_id, pid)


@router.put("/api/posts/{pid}/studio-cover")
async def studio_cover_save(
    pid: int,
    request: Request,
    file: UploadFile = File(...),
    title: str = Form("", max_length=100),
    logo_text: str = Form("", max_length=60),
    primary_color: str = Form("#ffffff", pattern=r"^#[0-9a-fA-F]{6}$"),
    accent_color: str = Form("#ffe15a", pattern=r"^#[0-9a-fA-F]{6}$"),
    font_family: str = Form("Roboto", max_length=20),
    ctx: Ctx = Depends(current),
):
    ws = ctx.workspace_id
    _post(ws, pid)
    _unlocked(ws, pid)
    if font_family not in STUDIO_FONTS:
        raise HTTPException(400, "Choose a supported cover font.")
    limit = max_image_bytes()
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 8192:
        raise HTTPException(413, f"That cover is too large. The limit is {limit // (1024 * 1024)} MB.")
    folder = UPLOAD_ROOT / str(ws) / "studio-covers" / str(pid)
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
                    kind = sniff_image(chunk[:16])
                    if kind is None:
                        raise HTTPException(415, "The cover must be a JPEG, PNG or WebP image.")
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"That cover is too large. The limit is {limit // (1024 * 1024)} MB.")
                out.write(chunk)
        if size == 0 or kind is None:
            raise HTTPException(400, "The cover image is empty.")
        final = folder / f"{secrets.token_urlsafe(18)}.{kind}"
        os.replace(tmp, final)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise

    relative_path = f"{ws}/studio-covers/{pid}/{final.name}"
    name = f"cover.{kind}"
    try:
        old = db.one("SELECT file_path FROM post_studio_covers WHERE post_id=%s AND workspace_id=%s", (pid, ws))
        db.one(
            """INSERT INTO post_studio_covers
                   (workspace_id, post_id, file_path, file_name, file_size, content_type,
                    title, logo_text, primary_color, accent_color, font_family, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
               ON CONFLICT (post_id) DO UPDATE SET
                   workspace_id=EXCLUDED.workspace_id, file_path=EXCLUDED.file_path,
                   file_name=EXCLUDED.file_name, file_size=EXCLUDED.file_size,
                   content_type=EXCLUDED.content_type, title=EXCLUDED.title,
                   logo_text=EXCLUDED.logo_text, primary_color=EXCLUDED.primary_color,
                   accent_color=EXCLUDED.accent_color, font_family=EXCLUDED.font_family,
                   updated_at=now()
               RETURNING file_path""",
            (ws, pid, relative_path, name, size, IMAGE_TYPES[kind], title.strip(), logo_text.strip(),
             primary_color.lower(), accent_color.lower(), font_family))
    except Exception:
        try:
            final.unlink()
        except OSError:
            pass
        raise
    if old and old.get("file_path") != relative_path:
        previous = _abs(old["file_path"])
        if previous:
            try:
                previous.unlink()
            except OSError:
                pass
    return get_studio_cover(ws, pid)


@router.get("/api/posts/{pid}/studio-cover/file")
def studio_cover_file(pid: int, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    row = db.one(
        "SELECT file_path, content_type FROM post_studio_covers WHERE post_id=%s AND workspace_id=%s",
        (pid, ctx.workspace_id))
    f = _abs(row["file_path"]) if row else None
    if not f:
        raise HTTPException(404, "No saved Studio cover for this post.")
    return FileResponse(f, media_type=row["content_type"] or "image/png",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.delete("/api/posts/{pid}/studio-cover")
def studio_cover_delete(pid: int, ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    _post(ws, pid)
    _unlocked(ws, pid)
    row = db.one("SELECT file_path FROM post_studio_covers WHERE post_id=%s AND workspace_id=%s", (pid, ws))
    if row is None:
        raise HTTPException(404, "No saved Studio cover for this post.")
    db.execute("DELETE FROM post_studio_covers WHERE post_id=%s AND workspace_id=%s", (pid, ws))
    cover_file = _abs(row["file_path"])
    if cover_file:
        try:
            cover_file.unlink()
        except OSError:
            pass
    shutil.rmtree(UPLOAD_ROOT / str(ws) / "studio-covers" / str(pid), ignore_errors=True)
    return {"ok": True}


@router.get("/media/i/{ws}/{pid}/{media_id}/{exp}/{sig}")
def signed_image(ws: int, pid: int, media_id: int, exp: int, sig: str):
    """What a publishing provider downloads for one image. No cookie: the signature is the credential."""
    row = db.one("SELECT file_path, content_type FROM post_media WHERE id=%s AND post_id=%s AND workspace_id=%s",
                 (media_id, pid, ws))
    if (not row or exp < int(time.time())
            or not hmac.compare_digest(sig, _sign(ws, pid, row["file_path"], exp))):
        raise HTTPException(404, "Not found")
    f = _abs(row["file_path"])
    if not f:
        raise HTTPException(404, "Not found")
    return FileResponse(f, media_type=row["content_type"] or "image/jpeg",
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


@router.get("/media/a/{ws}/{pid}/{audio_id}/{exp}/{sig}")
def signed_audio(ws: int, pid: int, audio_id: int, exp: int, sig: str):
    """What Shotstack's cloud render downloads for a Studio audio clip. No cookie: the signature is the credential."""
    row = db.one("SELECT file_path, content_type FROM post_audio WHERE id=%s AND post_id=%s AND workspace_id=%s",
                 (audio_id, pid, ws))
    if (not row or exp < int(time.time())
            or not hmac.compare_digest(sig, _sign(ws, pid, row["file_path"], exp))):
        raise HTTPException(404, "Not found")
    f = _abs(row["file_path"])
    if not f:
        raise HTTPException(404, "Not found")
    return FileResponse(f, media_type=row["content_type"] or "audio/mpeg",
                        headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


# ---------- Studio audio: voiceover/music clips added in the editor, not part of a post's real media ----------

def list_audio(ws: int, pid: int) -> list[dict]:
    return db.query(
        "SELECT id, file_name, file_size, content_type, position FROM post_audio "
        "WHERE post_id=%s AND workspace_id=%s ORDER BY position, id", (pid, ws))


@router.post("/api/posts/{pid}/studio-audio/upload")
async def studio_audio_upload(pid: int, request: Request, file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    ws = ctx.workspace_id
    _post(ws, pid)
    limit = max_image_bytes() * 5    # audio runs bigger than a photo but doesn't need its own env var yet
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 4096:
        raise HTTPException(413, f"That file is too large. The limit is {limit // (1024 * 1024)} MB.")
    folder = UPLOAD_ROOT / str(ws) / "studio-audio" / str(pid)
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
                    kind = sniff_audio(chunk[:16])
                    if kind is None:
                        raise HTTPException(415, "That is not a supported audio file. Upload an MP3, WAV, M4A or OGG file.")
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
    name = (file.filename or "audio").replace("\\", "/").rsplit("/", 1)[-1][:120]
    next_pos = db.one("SELECT COALESCE(MAX(position), -1) + 1 AS n FROM post_audio WHERE post_id=%s AND workspace_id=%s",
                      (pid, ws))["n"]
    row = db.one(
        """INSERT INTO post_audio (workspace_id, post_id, position, file_path, file_name, file_size, content_type)
           VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
        (ws, pid, next_pos, f"{ws}/studio-audio/{pid}/{final.name}", name, size, AUDIO_TYPES[kind]))
    return {"id": row["id"], "name": name, "size": size, "type": AUDIO_TYPES[kind], "position": next_pos}


@router.get("/api/posts/{pid}/studio-audio/{audio_id}/file")
@router.get("/api/posts/{pid}/studio-audio/{audio_id}/file.{ext}")
@router.head("/api/posts/{pid}/studio-audio/{audio_id}/file")
@router.head("/api/posts/{pid}/studio-audio/{audio_id}/file.{ext}")
def studio_audio_file(pid: int, audio_id: int, ctx: Ctx = Depends(current), ext: str = ""):
    row = db.one("SELECT * FROM post_audio WHERE id=%s AND post_id=%s AND workspace_id=%s",
                 (audio_id, pid, ctx.workspace_id))
    if row is None:
        raise HTTPException(404, "Audio file not found")
    f = _abs(row["file_path"])
    if not f:
        raise HTTPException(404, "Audio file not found")
    return FileResponse(f, media_type=row["content_type"] or "audio/mpeg",
                        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


# ---------- Studio draft: in-progress editor state, separate from the post's real video/images ----------

class StudioDraftIn(BaseModel):
    edit: dict = Field(..., description="The Edit JSON as Studio currently has it, to resume later")


@router.put("/api/posts/{pid}/studio-draft")
def save_studio_draft(pid: int, b: StudioDraftIn, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    row = db.one(
        "UPDATE posts SET studio_draft=%s, studio_draft_at=now() WHERE id=%s AND workspace_id=%s "
        "RETURNING studio_draft_at", (db.Json(b.edit), pid, ctx.workspace_id))
    return {"ok": True, "saved_at": row["studio_draft_at"]}


@router.delete("/api/posts/{pid}/studio-draft")
def clear_studio_draft(pid: int, ctx: Ctx = Depends(current)):
    _post(ctx.workspace_id, pid)
    db.execute("UPDATE posts SET studio_draft=NULL, studio_draft_at=NULL WHERE id=%s AND workspace_id=%s",
               (pid, ctx.workspace_id))
    return {"ok": True}
