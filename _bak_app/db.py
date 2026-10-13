"""Postgres access: a small thread-safe pool plus dict-row helpers."""
import os
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool

DEFAULT_URL = "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack"
_pool = None
_pool_url = None


def database_url() -> str:
    """DATABASE_URL, accepting SQLAlchemy-style schemes (postgresql+asyncpg://, postgres://) too."""
    url = os.environ.get("DATABASE_URL", DEFAULT_URL).strip()
    scheme, sep, rest = url.partition("://")
    if sep and (scheme.startswith("postgresql+") or scheme == "postgres"):
        url = "postgresql://" + rest
    return url


def _get_pool() -> ThreadedConnectionPool:
    global _pool, _pool_url
    url = database_url()
    if _pool is None or _pool_url != url:
        if _pool is not None:
            _pool.closeall()
        _pool = ThreadedConnectionPool(1, 10, url)
        _pool_url = url
    return _pool


@contextmanager
def conn():
    pool = _get_pool()
    c = pool.getconn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        pool.putconn(c)


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
