"""Shared Redis connection used for rate limiting, caching and job queues.

Redis is optional. On a host without it the import itself can fail, and that
must not stop the application from starting: rate limiting is a safety net on
top of authentication, not a prerequisite for serving requests.
"""
from __future__ import annotations

from .config import settings

try:  # pragma: no cover - exercised only where the package is absent
    import redis
except ImportError:  # pragma: no cover
    redis = None  # type: ignore[assignment]

_pool = None
if redis is not None:
    try:
        _pool = redis.ConnectionPool.from_url(settings.redis_url, decode_responses=True)
    except Exception:  # pragma: no cover - a malformed URL must not break startup
        _pool = None


def get_redis():
    if redis is None or _pool is None:
        raise RuntimeError("redis is not configured")
    return redis.Redis(connection_pool=_pool)


def redis_available() -> bool:
    if redis is None or _pool is None:
        return False
    try:
        return bool(get_redis().ping())
    except Exception:
        return False
