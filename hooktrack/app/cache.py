"""A small read cache for the expensive, repeated lookups, on Redis when REDIS_URL is set and in memory otherwise.

Design rules, because a stale page is worse than a slow one:
  * Every entry belongs to a workspace and carries that workspace's VERSION in its key. Any successful write through the
    API, and every background job that changes a workspace's data, bumps the version, which makes everything cached for
    that workspace unreachable at once. No hunting for individual keys to delete.
  * Every entry also expires (a short TTL) as a safety net for writes that were missed.
  * Redis being down is never an error: calls fall back to in-memory for 30 seconds, then try Redis again.
  * Only JSON is stored (never pickle), so a compromised cache cannot run code.
  * Nothing about sessions or permissions is cached.

With Redis, all processes (the app, the admin console, workers) share one cache and one version counter. Without it each
process has its own memory cache, which is correct for a single process and at most one TTL stale across several.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("hooktrack.cache")

PREFIX = "ht:"
LOCAL_MAX = 2000
RETRY_AFTER_S = 30

_lock = threading.Lock()
_local: dict = {}                      # key -> (expires_at, json string)
_local_versions: dict = {}             # workspace id -> int
_redis = None
_redis_url: Optional[str] = None
_down_until = 0.0
_stats = {"hits": 0, "misses": 0, "errors": 0}


def enabled() -> bool:
    return os.environ.get("HOOKTRACK_CACHE", "1").strip().lower() not in ("0", "false", "off", "no")


def _client():
    """The Redis client, or None when there is none or it is unreachable right now."""
    global _redis, _redis_url, _down_until
    url = (os.environ.get("REDIS_URL") or "").strip()
    if not url:
        return None
    if time.time() < _down_until:
        return None
    if _redis is None or _redis_url != url:
        try:
            import redis
            _redis = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=1.0,
                                          decode_responses=True, health_check_interval=30)
            _redis.ping()
            _redis_url = url
            log.info("cache: using Redis")
        except Exception as e:
            _redis = None
            _down_until = time.time() + RETRY_AFTER_S
            _stats["errors"] += 1
            log.warning("cache: Redis unavailable (%s); using memory for %ss", type(e).__name__, RETRY_AFTER_S)
            return None
    return _redis


def _trip(e: Exception) -> None:
    global _down_until, _redis
    _stats["errors"] += 1
    _down_until = time.time() + RETRY_AFTER_S
    _redis = None
    log.warning("cache: Redis error (%s); using memory for %ss", type(e).__name__, RETRY_AFTER_S)


# ---------- JSON that keeps integer keys (performance() is keyed by post id) ----------

def _enc(o: Any):
    if isinstance(o, dict):
        if o and all(isinstance(k, int) and not isinstance(k, bool) for k in o):
            return {"__ik": [[k, _enc(v)] for k, v in o.items()]}
        return {str(k): _enc(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_enc(v) for v in o]
    return o


def _dec(o: Any):
    if isinstance(o, dict):
        if set(o) == {"__ik"}:
            return {int(k): _dec(v) for k, v in o["__ik"]}
        return {k: _dec(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_dec(v) for v in o]
    return o


def dumps(value: Any) -> str:
    return json.dumps(_enc(value), default=str, separators=(",", ":"))


def loads(text: str) -> Any:
    return _dec(json.loads(text))


# ---------- raw get / set ----------

def _get_raw(key: str) -> Optional[str]:
    c = _client()
    if c is not None:
        try:
            return c.get(key)
        except Exception as e:
            _trip(e)
    with _lock:
        hit = _local.get(key)
        if hit and hit[0] > time.time():
            return hit[1]
        _local.pop(key, None)
    return None


def _set_raw(key: str, text: str, ttl: int) -> None:
    c = _client()
    if c is not None:
        try:
            c.set(key, text, ex=max(1, int(ttl)))
            return
        except Exception as e:
            _trip(e)
    with _lock:
        if len(_local) >= LOCAL_MAX:
            now = time.time()
            for k in [k for k, v in _local.items() if v[0] <= now][:LOCAL_MAX // 2] or list(_local)[:LOCAL_MAX // 4]:
                _local.pop(k, None)
        _local[key] = (time.time() + ttl, text)


def _delete_raw(key: str) -> None:
    c = _client()
    if c is not None:
        try:
            c.delete(key)
        except Exception as e:
            _trip(e)
    with _lock:
        _local.pop(key, None)


# ---------- workspace versions ----------

def version(ws: int) -> int:
    c = _client()
    if c is not None:
        try:
            v = c.get(f"{PREFIX}v:{ws}")
            return int(v) if v else 0
        except Exception as e:
            _trip(e)
    with _lock:
        return _local_versions.get(ws, 0)


def bump(ws: Optional[int]) -> None:
    """Everything cached for this workspace is stale from now on. Call after any change to its data."""
    if not ws:
        return
    c = _client()
    if c is not None:
        try:
            c.incr(f"{PREFIX}v:{ws}")
        except Exception as e:
            _trip(e)
    with _lock:
        _local_versions[ws] = _local_versions.get(ws, 0) + 1


# ---------- the decorator ----------

def _arg_key(args: tuple, kwargs: dict) -> str:
    return hashlib.sha1(json.dumps([args, sorted(kwargs.items())], default=str).encode()).hexdigest()[:16]


def workspace_cached(name: str, ttl: int = 60) -> Callable:
    """Cache a function whose FIRST argument is the workspace id. The result must be JSON-safe (dates become text)."""
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(ws, *args, **kwargs):
            if not enabled() or not isinstance(ws, int):
                return fn(ws, *args, **kwargs)
            key = f"{PREFIX}{ws}:{version(ws)}:{name}:{_arg_key(args, kwargs)}"
            raw = _get_raw(key)
            if raw is not None:
                try:
                    _stats["hits"] += 1
                    return loads(raw)
                except ValueError:
                    pass
            _stats["misses"] += 1
            started = version(ws)
            out = fn(ws, *args, **kwargs)
            if version(ws) == started:          # a write landed while we computed: do not cache a possibly stale answer
                try:
                    _set_raw(key, dumps(out), ttl)
                except (TypeError, ValueError):
                    pass
            return out
        wrapper.__wrapped_uncached__ = fn
        return wrapper
    return deco


# ---------- platform-wide values (not tied to a workspace) ----------

def get_platform(key: str) -> tuple[bool, Any]:
    """(found, value) for a platform-level setting cached under `key`."""
    if not enabled():
        return False, None
    raw = _get_raw(f"{PREFIX}p:{key}")
    return (True, loads(raw)) if raw is not None else (False, None)


def set_platform(key: str, value: Any, ttl: int = 30) -> None:
    if enabled():
        _set_raw(f"{PREFIX}p:{key}", dumps(value), ttl)


def drop_platform(key: str) -> None:
    _delete_raw(f"{PREFIX}p:{key}")


def status() -> dict:
    """For the admin console and logs: which backend is in use and how it is doing."""
    c = _client() if (os.environ.get("REDIS_URL") or "").strip() else None
    total = _stats["hits"] + _stats["misses"]
    return {"enabled": enabled(), "backend": "redis" if c is not None else "memory",
            "redis_configured": bool((os.environ.get("REDIS_URL") or "").strip()),
            "hits": _stats["hits"], "misses": _stats["misses"], "errors": _stats["errors"],
            "hit_rate": round(_stats["hits"] / total, 3) if total else None}
