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

# Behind a proxy (Cloudflare, Fly, any load balancer) every request arrives from
# the proxy's address, so limiting on the socket peer would put all users in one
# bucket. The forwarded headers are only read when the deployment says a trusted
# proxy is in front: otherwise any client could set them and pick its own key.
_CLIENT_IP_HEADERS = ("cf-connecting-ip", "x-real-ip", "x-forwarded-for")


def _client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        for header in _CLIENT_IP_HEADERS:
            value = request.headers.get(header)
            if not value:
                continue
            # X-Forwarded-For is a chain; the first entry is the original client.
            candidate = value.split(",")[0].strip()
            if candidate:
                return candidate
    return request.client.host if request.client else "unknown"


def _bucket_for(path: str) -> tuple[str, int]:
    """The counter a request belongs to, and its ceiling.

    Auth endpoints get their own counter. Sharing one counter with the rest of
    the API made the two limits interfere: a page that loads a dozen records
    would exhaust the auth allowance and lock the user out of signing in, which
    is the opposite of what a login throttle is for.
    """
    if path.endswith("/auth/login") or path.endswith("/auth/register"):
        return "auth", settings.login_rate_limit_per_minute
    return "api", settings.rate_limit_per_minute


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        global _redis_ok
        if request.url.path.startswith(("/static", "/health", "/docs", "/openapi.json")):
            return await call_next(request)

        if _redis_ok is None:
            _redis_ok = redis_available()
        if not _redis_ok:
            return await call_next(request)

        client = _client_ip(request)
        window = int(time.time() // 60)
        bucket, limit = _bucket_for(request.url.path)
        key = f"rl:{bucket}:{client}:{window}"
        try:
            redis = get_redis()
            count = redis.incr(key)
            if count == 1:
                redis.expire(key, 65)
            if count > limit:
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={"detail": {"code": "rate_limited", "message": "Too many requests, slow down"}},
                )
        except Exception:
            _redis_ok = False
        return await call_next(request)
