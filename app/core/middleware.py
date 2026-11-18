"""Redis-backed fixed-window rate limiting middleware.

Fails open if Redis is unavailable: an outage of the cache should not take the
whole application down, and the limits here are a safety net rather than the
primary control (that is authentication).
"""
from __future__ import annotations

import time

from fastapi import Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import settings
from .redis_client import get_redis, redis_available

_redis_ok: bool | None = None


def _limit_for(path: str) -> int:
    if path.endswith("/auth/login") or path.endswith("/auth/register"):
        return settings.login_rate_limit_per_minute
    return settings.rate_limit_per_minute


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        global _redis_ok
        if request.url.path.startswith(("/static", "/health", "/docs", "/openapi.json")):
            return await call_next(request)

        if _redis_ok is None:
            _redis_ok = redis_available()
        if not _redis_ok:
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        window = int(time.time() // 60)
        key = f"rl:{client}:{window}"
        try:
            redis = get_redis()
            count = redis.incr(key)
            if count == 1:
                redis.expire(key, 65)
            if count > _limit_for(request.url.path):
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": {"code": "rate_limited", "message": "Too many requests, slow down"}},
                )
        except Exception:
            _redis_ok = False
        return await call_next(request)
