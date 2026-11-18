"""Shared Redis connection used for rate limiting, caching and job queues."""
from __future__ import annotations

import redis

from .config import settings

_pool = redis.ConnectionPool.from_url(settings.redis_url, decode_responses=True)


def get_redis() -> redis.Redis:
    return redis.Redis(connection_pool=_pool)


def redis_available() -> bool:
    try:
        return bool(get_redis().ping())
    except redis.RedisError:
        return False
