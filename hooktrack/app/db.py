"""Postgres access: a small thread-safe pool plus dict-row helpers."""
import os
import threading
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool

DEFAULT_URL = "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack"
_pool = None
_pool_url = None
_slots = None   # caps concurrent users of the pool so extra requests wait instead of failing
POOL_MAX = max(2, int(os.environ.get("HOOKTRACK_DB_POOL", "20") or 20))
POOL_WAIT = float(os.environ.get("HOOKTRACK_DB_POOL_WAIT", "30") or 30)


def database_url() -> str:
    """DATABASE_URL, accepting SQLAlchemy-style schemes (postgresql+asyncpg://, postgres://) too."""
    url = os.environ.get("DATABASE_URL", DEFAULT_URL).strip()
    scheme, sep, rest = url.partition("://")
    if sep and (scheme.startswith("postgresql+") or scheme == "postgres"):
        url = "postgresql://" + rest
    return url


def _get_pool() -> ThreadedConnectionPool:
    global _pool, _pool_url, _slots
    url = database_url()
    if _pool is None or _pool_url != url:
        if _pool is not None:
            _pool.closeall()
        _pool = ThreadedConnectionPool(1, POOL_MAX, url)
        _slots = threading.BoundedSemaphore(POOL_MAX)
        _pool_url = url
    return _pool


@contextmanager
def conn():
    pool = _get_pool()
    slots = _slots
    if not slots.acquire(timeout=POOL_WAIT):
        raise RuntimeError("database connection pool busy; try again")
    try:
        c = pool.getconn()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            pool.putconn(c)
    finally:
        slots.release()


def query(sql, params=None):
    with conn() as c, c.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params or ())
        return cur.fetchall() if cur.description else []


def one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.rowcount


def executemany(sql, rows):
    with conn() as c, c.cursor() as cur:
        psycopg2.extras.execute_batch(cur, sql, rows)


def init_schema():
    """Bring the database up to date (versioned migrations in app/migrations/)."""
    from . import migrate
    migrate.up(database_url())


__all__ = ["query", "one", "execute", "executemany", "init_schema", "conn", "Json"]
