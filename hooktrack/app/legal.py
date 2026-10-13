"""Operator-editable details shown on the public Terms and Privacy pages."""
from __future__ import annotations

import re

from fastapi import HTTPException

from . import db

TERMS_VERSION = "2026-10-10"        # bump when the Terms change materially
KEY = "legal_details"
FIELDS = {"company": 120, "email": 200, "address": 300, "jurisdiction": 160, "refund_policy": 600}
EMAIL_RE = re.compile(r"^[^@\s<>\"]+@[^@\s<>\"]+\.[^@\s<>\"]+$")


def get() -> dict:
    row = db.one("SELECT value FROM platform_settings WHERE key=%s", (KEY,))
    raw = (row or {}).get("value") or {}
    return {k: str(raw.get(k, "") or "").strip() for k in FIELDS}


def save(values: dict) -> dict:
    cur = get()
    for k, limit in FIELDS.items():
        if k not in values or values[k] is None:
            continue
        v = re.sub(r"[<>]", "", str(values[k])).strip()
        if len(v) > limit:
            raise HTTPException(400, f"{k.replace('_', ' ').capitalize()} is too long (max {limit} characters)")
        if k == "email" and v and not EMAIL_RE.match(v):
            raise HTTPException(400, "Enter a valid contact email address")
        cur[k] = v
    db.execute(
        """INSERT INTO platform_settings(key, value, updated_at) VALUES (%s, %s, now())
           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()""", (KEY, db.Json(cur)))
    return cur


def configured() -> bool:
    """True once the operator has said who is behind the service and how to reach them."""
    d = get()
    return bool(d["company"] and d["email"])
