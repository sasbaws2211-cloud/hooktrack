"""Cross-process leases in Postgres: "only one worker runs this at a time".

A lease is a row in job_leases with an expiry. Whoever inserts it (or takes over an expired one) owns it;
long jobs keep renewing it from a heartbeat thread, and a crashed owner simply stops renewing, so the
lease frees itself after `ttl` seconds. Leases are not re-entrant: a second attempt by the same process
fails too, which is what stops a manual run from overlapping a scheduled one.
"""
import logging
import os
import socket
import threading
import uuid
from contextlib import contextmanager

from . import db

log = logging.getLogger("hooktrack.leases")
HOLDER = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def acquire(name: str, ttl: float) -> bool:
    row = db.one(
        """INSERT INTO job_leases(name, holder, expires_at)
           VALUES (%s, %s, now() + make_interval(secs => %s))
           ON CONFLICT (name) DO UPDATE
             SET holder = EXCLUDED.holder, expires_at = EXCLUDED.expires_at, acquired_at = now()
             WHERE job_leases.expires_at < now()
           RETURNING name""",
        (name, HOLDER, ttl))
    return row is not None


def renew(name: str, ttl: float) -> bool:
    return db.one(
        """UPDATE job_leases SET expires_at = now() + make_interval(secs => %s)
           WHERE name = %s AND holder = %s AND expires_at > now() RETURNING name""",
        (ttl, name, HOLDER)) is not None


def release(name: str) -> None:
    db.execute("DELETE FROM job_leases WHERE name = %s AND holder = %s", (name, HOLDER))


def is_held(name: str) -> bool:
    """True while some worker (any process) holds an unexpired lease."""
    return db.one("SELECT 1 AS x FROM job_leases WHERE name = %s AND expires_at > now()", (name,)) is not None


@contextmanager
def lease(name: str, ttl: float = 300):
    """`with lease("x") as got:` -> got is False if another worker holds it (then do nothing)."""
    if not acquire(name, ttl):
        yield False
        return
    stop = threading.Event()

    def beat():
        while not stop.wait(max(1.0, ttl / 3)):
            try:
                if not renew(name, ttl):
                    log.warning("lost lease %s (it expired while the job was still running)", name)
                    return
            except Exception:
                log.exception("renewing lease %s failed", name)

    t = threading.Thread(target=beat, name=f"lease-{name}", daemon=True)
    t.start()
    try:
        yield True
    finally:
        stop.set()
        try:
            release(name)
        except Exception:
            log.exception("releasing lease %s failed", name)
