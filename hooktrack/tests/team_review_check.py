from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from fastapi.testclient import TestClient

from app import auth, main, team_review
from app.auth import Ctx


def _ctx(role="member"):
    return Ctx(user_id=12, email="teammate@example.com", name="Teammate", workspace_id=7, role=role)


@pytest.mark.parametrize("status,blocked", [
    ("not_requested", False),
    ("pending", True),
    ("changes_requested", True),
    ("approved", False),
])
def test_review_gate_only_blocks_open_review(status, blocked):
    assert team_review.is_publish_blocked({"team_review_status": status}) is blocked


def test_assignment_must_be_a_workspace_member(monkeypatch):
    monkeypatch.setattr(main.db, "one", lambda *_: None)
    with pytest.raises(HTTPException) as exc:
        main._own_member(7, 42)
    assert exc.value.status_code == 400
    assert "member of this workspace" in exc.value.detail


def test_only_admins_can_submit_review_decisions():
    main.app.dependency_overrides[auth.current] = lambda: _ctx("member")
    try:
        response = TestClient(main.app).post(
            "/api/posts/31/team-review/decision", json={"action": "approve"})
    finally:
        main.app.dependency_overrides.pop(auth.current, None)
    assert response.status_code == 403


def test_request_review_records_requester_and_pending_status(monkeypatch):
    timestamp = datetime.now(timezone.utc)
    results = iter([
        {"id": 31, "external_id": None, "external_post_id": "", "status": "filmed",
         "scheduled_for": None, "team_review_status": "changes_requested"},
        None,
        None,
        {"id": 31},
        {"team_review_status": "pending", "team_review_note": "", "team_review_requested_at": timestamp,
         "team_reviewed_at": None, "team_review_requested_by": 12, "team_reviewed_by": None,
         "team_review_requester_name": "Teammate", "team_reviewer_name": None},
    ])
    calls = []
    monkeypatch.setattr(team_review.db, "one", lambda sql, params=(): calls.append((sql, params)) or next(results))

    result = team_review.request_review(31, _ctx())

    assert result["team_review_status"] == "pending"
    assert result["team_review_requested_by"] == 12
    assert result["team_review_requested_at"] == timestamp.isoformat()
    assert "team_review_status='pending'" in calls[3][0]
    assert "team_review_status IN ('not_requested','changes_requested')" in calls[3][0]


def test_scheduled_post_cannot_enter_review(monkeypatch):
    monkeypatch.setattr(team_review.db, "one", lambda *_: {
        "id": 31, "external_id": None, "external_post_id": "", "status": "filmed",
        "scheduled_for": datetime.now(timezone.utc), "team_review_status": "not_requested",
    })
    with pytest.raises(HTTPException) as exc:
        team_review.request_review(31, _ctx())
    assert exc.value.status_code == 409
    assert "Cancel the scheduled publish" in exc.value.detail


def test_decision_requires_review_pending_and_saves_change_note(monkeypatch):
    results = iter([
        {"id": 31},
        {"team_review_status": "changes_requested", "team_review_note": "Shorten the caption.",
         "team_review_requested_at": None, "team_reviewed_at": None, "team_review_requested_by": 12,
         "team_reviewed_by": 88, "team_review_requester_name": "Teammate", "team_reviewer_name": "Admin"},
    ])
    calls = []
    monkeypatch.setattr(team_review.db, "one", lambda sql, params=(): calls.append((sql, params)) or next(results))

    result = team_review.decide_review(
        31, team_review.ReviewDecisionIn(action="request_changes", note=" Shorten the caption. "), _ctx("admin"))

    assert result["team_review_status"] == "changes_requested"
    assert result["team_review_note"] == "Shorten the caption."
    assert calls[0][1] == ("changes_requested", "Shorten the caption.", 12, 31, 7)
    assert "team_review_status='pending'" in calls[0][0]


def test_content_edit_resets_approval_and_cancels_pending_client_review(monkeypatch):
    updates = []
    cancellations = []
    monkeypatch.setattr(main.db, "one", lambda sql, params=(): updates.append((sql, params)) or {"id": 31})
    monkeypatch.setattr(main.db, "execute", lambda sql, params=(): cancellations.append((sql, params)))

    main._update_post(31, 7, {"caption": "Updated caption"}, invalidate_review=True)

    update_sql, update_params = updates[0]
    assert "team_review_status=CASE WHEN team_review_status IN ('pending','approved')" in update_sql
    assert "team_reviewed_at=CASE" in update_sql
    assert update_params == ("Updated caption", "Post content changed after review. Submit it for review again.", 31, 7)
    assert cancellations == [("UPDATE post_approvals SET status='cancelled' WHERE post_id=%s AND status='pending'", (31,))]
