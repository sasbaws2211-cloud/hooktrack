"""Encryption for secrets stored in the database (per-workspace AI keys).

The Fernet key comes from HOOKTRACK_SECRET (any string) or, if unset, from an
auto-generated `.secret` file next to the app. Losing it means stored keys must be re-entered.
"""
import base64
import hashlib
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

SECRET_FILE = Path(__file__).parent.parent / ".secret"
_fernet = None


def _load_secret() -> str:
    env = os.environ.get("HOOKTRACK_SECRET")
    if env:
        return env
    if not SECRET_FILE.exists():
        SECRET_FILE.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return SECRET_FILE.read_text(encoding="utf-8").strip()


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        digest = hashlib.sha256(_load_secret().encode()).digest()
        _fernet = Fernet(base64.urlsafe_b64encode(digest))
    return _fernet


def encrypt(text: str) -> str:
    return _f().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    """Returns '' if the token can't be decrypted (e.g. the secret changed)."""
    try:
        return _f().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
