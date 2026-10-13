"""Postgres access: a small thread-safe pool plus dict-row helpers."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, Json
from psycopg2.pool import ThreadedConnectionPool

DEFAULT_URL = "postgresql://hooktrack:ht_dev_pw@127.0.0.1:55434/hooktrack"
_pool = None
_pool_url = None


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


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
    sql = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with conn() as c, c.cursor() as cur:
        cur.execute(sql)


__all__ = ["query", "one", "execute", "executemany", "init_schema", "conn", "Json"]
