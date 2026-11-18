import os
import sys
from datetime import date

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import billing


def _enable_limited_plan(monkeypatch, limit=2):
    month = date(2026, 6, 1)
    monkeypatch.setattr(billing, "enabled", lambda: True)
    monkeypatch.setattr(billing, "require_active", lambda _workspace_id: None)
    monkeypatch.setattr(billing, "effective_plan", lambda _workspace_id: {
        "name": "Test", "max_ai_calls": limit,
    })
    monkeypatch.setattr(billing, "_month", lambda: month)
    return month


def test_ai_reservation_uses_atomic_monthly_limit(monkeypatch):
    month = _enable_limited_plan(monkeypatch)
    queries = []
    monkeypatch.setattr(billing.db, "one", lambda query, params: queries.append((query, params)) or {"calls": 2})

    assert billing.reserve_ai(7) == month
    query, params = queries[0]
    assert "ON CONFLICT (workspace_id, month) DO UPDATE" in query
    assert "WHERE ai_usage.calls < %s" in query
    assert params == (7, month, 2, 2)


def test_ai_reservation_refuses_when_limit_is_exhausted(monkeypatch):
    _enable_limited_plan(monkeypatch)
    monkeypatch.setattr(billing.db, "one", lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as error:
        billing.reserve_ai(7)

    assert error.value.status_code == 402
    assert "2 AI requests a month" in error.value.detail


def test_unlimited_ai_plan_does_not_reserve_quota(monkeypatch):
    _enable_limited_plan(monkeypatch, limit=-1)
    monkeypatch.setattr(billing.db, "one", lambda *_args, **_kwargs:
                        pytest.fail("Unlimited requests should not reserve a quota row"))

    assert billing.reserve_ai(7) is None
