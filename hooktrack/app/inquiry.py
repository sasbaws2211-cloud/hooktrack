"""The "need more?" form on the pricing section: someone running more than the plans cover tells us what they need.

It is emailed to the sales address (admin@hooktrack.site unless HOOKTRACK_SALES_EMAIL says otherwise) and also saved, so a failed
email loses nothing. No account is needed. Protection: a hidden honeypot field, a per-visitor limit, short validated fields, and
the visitor's own text is never put in a mail header or subject."""
from __future__ import annotations

import logging
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import auth, db, mailer
from .freeai import clean

log = logging.getLogger("hooktrack.inquiry")
router = APIRouter(prefix="/api/public", tags=["public"])
EMAIL_RE = re.compile(r"^[^@\s<>\"',;]{1,64}@[^@\s<>\"',;]{1,190}\.[A-Za-z]{2,24}$")
PER_VISITOR = 5          # messages per visitor per hour


def sales_email() -> str:
    return (os.environ.get("HOOKTRACK_SALES_EMAIL") or "admin@hooktrack.site").strip()


class NeedMoreIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    accounts: Optional[int] = Field(None, ge=0, le=100000)
    team: Optional[int] = Field(None, ge=0, le=10000)
    platforms: str = Field("", max_length=100)
    budget: str = Field("", max_length=60)
    message: str = Field("", max_length=1000)
    website: str = Field("", max_length=200)       # honeypot: people never see this field, bots fill it in


@router.post("/need-more")
def need_more(b: NeedMoreIn, request: Request):
    if b.website.strip():
        return {"ok": True}                         # a bot: look successful, do nothing
    email = clean(b.email, 254).replace("\n", "")
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Please enter a valid email address so we can reply.")
    auth._throttle(f"needmore:{auth._client_ip(request)}", PER_VISITOR, 3600)
    platforms, budget, message = clean(b.platforms, 100), clean(b.budget, 60), clean(b.message, 1000)
    row = db.one("INSERT INTO sales_inquiries(email, accounts, team, platforms, budget, message) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                 (email, b.accounts, b.team, platforms, budget, message))
    body = (f"Someone asked for more than the plans cover.\n\nEmail: {email}\nTracked accounts: {b.accounts if b.accounts is not None else 'not given'}\n"
            f"Team size: {b.team if b.team is not None else 'not given'}\nPlatforms: {platforms or 'not given'}\n"
            f"Budget: {budget or 'not given'}\n\nMessage:\n{message or '(none)'}\n\nReply to them directly at {email}.")
    sent = False
    try:
        sent = mailer.send(sales_email(), "HookTrack: a visitor needs more than the plans", body)
    except Exception:
        log.exception("could not email the need-more message")
    if sent:
        db.execute("UPDATE sales_inquiries SET emailed=TRUE WHERE id=%s", (row["id"],))
    else:
        log.warning("need-more message %s was saved but not emailed", row["id"])
    return {"ok": True}
