"""Optional outbound email through Resend (https://resend.com). Not configured = nothing is sent.

RESEND_API_KEY             required to enable email (re_...)
HOOKTRACK_EMAIL_FROM       From address on a domain verified in Resend, e.g. "HookTrack <hello@yourdomain.com>"
                           (Resend's sandbox sender "onboarding@resend.dev" only delivers to your own account email)
HOOKTRACK_PUBLIC_URL       e.g. https://hooktrack.example.com, the base for links in emails
"""
import logging
import os

import httpx

log = logging.getLogger("hooktrack.mail")

RESEND_API = os.environ.get("HOOKTRACK_RESEND_API", "https://api.resend.com").rstrip("/")   # override only to point tests at a fake


def public_url() -> str:
    return (os.environ.get("HOOKTRACK_PUBLIC_URL") or "").strip().rstrip("/")


def configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY", "").strip() and public_url())


def send(to: str, subject: str, text: str) -> bool:
    """Send a plain-text email. Returns False (and logs) instead of raising: callers must not leak failures."""
    key = os.environ.get("RESEND_API_KEY", "").strip()
    if not key:
        return False
    sender = os.environ.get("HOOKTRACK_EMAIL_FROM", "").strip()
    if not sender:
        log.error("HOOKTRACK_EMAIL_FROM must be set to send email")
        return False
    try:
        r = httpx.post(f"{RESEND_API}/emails", timeout=20,
                       headers={"Authorization": f"Bearer {key}"},
                       json={"from": sender, "to": [to], "subject": subject, "text": text})
        if r.status_code >= 300:
            log.error("Resend rejected email to %s: %s %s", to, r.status_code, r.text[:300])
            return False
        return True
    except Exception:
        log.exception("sending email to %s failed", to)
        return False
