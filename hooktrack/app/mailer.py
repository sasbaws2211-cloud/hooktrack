"""Optional outbound email over SMTP (stdlib only). Not configured = nothing is sent.

HOOKTRACK_SMTP_HOST        required to enable email
HOOKTRACK_SMTP_PORT        default 587
HOOKTRACK_SMTP_USER / HOOKTRACK_SMTP_PASSWORD   optional login
HOOKTRACK_SMTP_SECURITY    starttls (default) | ssl | none
HOOKTRACK_SMTP_FROM        From address (default: the SMTP user)
HOOKTRACK_PUBLIC_URL       e.g. https://hooktrack.example.com, the base for links in emails
"""
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("hooktrack.mail")


def public_url() -> str:
    return (os.environ.get("HOOKTRACK_PUBLIC_URL") or "").strip().rstrip("/")


def configured() -> bool:
    return bool(os.environ.get("HOOKTRACK_SMTP_HOST", "").strip() and public_url())


def send(to: str, subject: str, text: str) -> bool:
    """Send a plain-text email. Returns False (and logs) instead of raising: callers must not leak failures."""
    host = os.environ.get("HOOKTRACK_SMTP_HOST", "").strip()
    if not host:
        return False
    user = os.environ.get("HOOKTRACK_SMTP_USER", "")
    sender = os.environ.get("HOOKTRACK_SMTP_FROM", "").strip() or user
    if not sender:
        log.error("HOOKTRACK_SMTP_FROM (or SMTP_USER) must be set to send email")
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, to, subject
    msg.set_content(text)
    mode = os.environ.get("HOOKTRACK_SMTP_SECURITY", "starttls").lower()
    port = int(os.environ.get("HOOKTRACK_SMTP_PORT") or (465 if mode == "ssl" else 587))
    try:
        if mode == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(host, port, timeout=20)
        with s:
            if mode == "starttls":
                s.starttls(context=ssl.create_default_context())
            if user:
                s.login(user, os.environ.get("HOOKTRACK_SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except Exception:
        log.exception("sending email to %s failed", to)
        return False
