"""Rate limiting must keep auth attempts separate from ordinary API traffic."""
from __future__ import annotations

import pytest

from app.core import middleware


class _FakeRedis:
    """Just enough of Redis for a fixed-window counter."""

    def __init__(self):
        self.counts: dict[str, int] = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, seconds):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    store = _FakeRedis()
    monkeypatch.setattr(middleware, "_redis_ok", True)
    monkeypatch.setattr(middleware, "get_redis", lambda: store)
    yield store
    middleware._redis_ok = None


def test_ordinary_traffic_does_not_exhaust_the_login_allowance(client, fake_redis, monkeypatch):
    """Loading a page must not lock the user out of signing in.

    One counter for every path made the smaller auth ceiling apply to the whole
    API: a dozen ordinary requests used up the login allowance and the next
    sign-in attempt was refused. Each class of request needs its own counter.
    """
    monkeypatch.setattr(middleware.settings, "rate_limit_per_minute", 1000)
    monkeypatch.setattr(middleware.settings, "login_rate_limit_per_minute", 2)

    for _ in range(10):
        client.get("/api/v1/accounts")

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@acme.eg", "password": "Str0ng!Pass"},
    )
    assert response.status_code != 429, "ordinary traffic consumed the login allowance"


def test_login_attempts_still_are_throttled(client, fake_redis, monkeypatch):
    """The protection itself must survive the fix."""
    monkeypatch.setattr(middleware.settings, "login_rate_limit_per_minute", 2)

    for _ in range(3):
        response = client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@acme.eg", "password": "Str0ng!Pass"},
        )
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
