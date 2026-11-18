"""Workspace-internal post assignment and approval workflow."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db
from .auth import Ctx, admin, current

router = APIRouter(prefix="/api")
BLOCKING_REVIEW_STATUSES = {"pending", "changes_requested"}


def is_publish_blocked(post: dict) -> bool:
    return post.get("team_review_status") in BLOCKING_REVIEW_STATUSES


def invalidate_after_content_edit(workspace_id: int, post_id: int) -> None:
    db.execute(
        """UPDATE posts SET team_review_status='changes_requested',
               team_review_note='Post content changed after review. Submit it for review again.',
               team_reviewed_by=NULL, team_reviewed_at=NULL
           WHERE id=%s AND workspace_id=%s AND team_review_status IN ('pending','approved')""",
        (post_id, workspace_id),
    )
    db.execute(
        "UPDATE post_approvals SET status='cancelled' WHERE post_id=%s AND status='pending'",
        (post_id,),
    )


def _review_out(row: dict) -> dict:
    out = dict(row)
    for key in ("team_review_requested_at", "team_reviewed_at"):
        if out.get(key) and hasattr(out[key], "isoformat"):
            out[key] = out[key].isoformat()
    return out


def _review(workspace_id: int, post_id: int) -> dict:
    row = db.one(
        """SELECT p.team_review_status, p.team_review_note, p.team_review_requested_at,
                  p.team_reviewed_at, p.team_review_requested_by, p.team_reviewed_by,
                  COALESCE(NULLIF(requester.name,''), requester.email) AS team_review_requester_name,
                  COALESCE(NULLIF(reviewer.name,''), reviewer.email) AS team_reviewer_name
           FROM posts p
           LEFT JOIN users requester ON requester.id=p.team_review_requested_by
           LEFT JOIN users reviewer ON reviewer.id=p.team_reviewed_by
           WHERE p.id=%s AND p.workspace_id=%s""",
        (post_id, workspace_id),
    )
    if not row:
        raise HTTPException(404, "Post not found")
    return _review_out(row)


class ReviewDecisionIn(BaseModel):
    action: str
    note: str = Field("", max_length=2000)


@router.post("/posts/{pid}/team-review/request")
def request_review(pid: int, ctx: Ctx = Depends(current)):
    post = db.one(
        "SELECT id, external_id, external_post_id, status, scheduled_for, team_review_status "
        "FROM posts WHERE id=%s AND workspace_id=%s",
        (pid, ctx.workspace_id),
    )
    if not post:
        raise HTTPException(404, "Post not found")
    if post.get("external_id"):
        raise HTTPException(400, "Only workspace posts can be sent for team review")
    if post.get("external_post_id") or post["status"] in ("posted", "analysed"):
        raise HTTPException(409, "Published posts cannot be sent for team review")
    if post.get("scheduled_for") or db.one(
            "SELECT 1 FROM publish_jobs WHERE workspace_id=%s AND post_id=%s AND status IN ('queued','scheduled')",
            (ctx.workspace_id, pid)):
        raise HTTPException(409, "Cancel the scheduled publish before requesting team review")
    if db.one(
            "SELECT 1 FROM post_approvals WHERE workspace_id=%s AND post_id=%s AND status='pending'",
            (ctx.workspace_id, pid)):
        raise HTTPException(409, "Cancel the pending client approval before requesting team review")
    if post["team_review_status"] == "approved":
        raise HTTPException(409, "This post is already approved")
    if post["team_review_status"] == "pending":
        raise HTTPException(409, "This post is already waiting for review")
    updated = db.one(
        """UPDATE posts SET team_review_status='pending', team_review_note='',
               team_review_requested_by=%s, team_review_requested_at=now(),
               team_reviewed_by=NULL, team_reviewed_at=NULL
           WHERE id=%s AND workspace_id=%s AND team_review_status IN ('not_requested','changes_requested')
           RETURNING id""",
        (ctx.user_id, pid, ctx.workspace_id),
    )
    if not updated:
        raise HTTPException(409, "This post's review status changed. Refresh and try again.")
    return _review(ctx.workspace_id, pid)


@router.post("/posts/{pid}/team-review/decision")
def decide_review(pid: int, body: ReviewDecisionIn, ctx: Ctx = Depends(admin)):
    if body.action not in ("approve", "request_changes"):
        raise HTTPException(400, "Action must be approve or request_changes")
    note = body.note.strip()
    if body.action == "request_changes" and not note:
        raise HTTPException(400, "Add a note describing the changes needed")
    status = "approved" if body.action == "approve" else "changes_requested"
    row = db.one(
        """UPDATE posts SET team_review_status=%s, team_review_note=%s,
               team_reviewed_by=%s, team_reviewed_at=now()
           WHERE id=%s AND workspace_id=%s AND team_review_status='pending'
           RETURNING id""",
        (status, note if status == "changes_requested" else "", ctx.user_id, pid, ctx.workspace_id),
    )
    if not row:
        exists = db.one("SELECT 1 FROM posts WHERE id=%s AND workspace_id=%s", (pid, ctx.workspace_id))
        if not exists:
            raise HTTPException(404, "Post not found")
        raise HTTPException(409, "This post is no longer waiting for review")
    return _review(ctx.workspace_id, pid)
