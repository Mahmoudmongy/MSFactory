"""The frontends must be able to reach a backend on a different origin.

These cover the three things that break when the screens are hosted apart from
the API: where the client sends its calls, whether the browser accepts the
cross-origin response, and whether a threadless runtime can still serve startup.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.api.v1 import bootstrap as bootstrap_module


def test_api_config_is_served_at_the_root(client):
    response = client.get("/api-config.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    # The frontends load this before their own modules, so it must define the
    # global they read rather than export anything.
    assert "MSFACTORY_API_BASE" in response.text


def test_api_config_defaults_to_same_origin(client):
    """Empty means "call the API on this same origin", the co-hosted default."""
    body = client.get("/api-config.js").text
    assignment = [
        line for line in body.splitlines() if line.strip().startswith("window.MSFACTORY_API_BASE")
    ]

    assert len(assignment) == 1
    value = assignment[0].split("=", 1)[1].strip().rstrip(";").strip()
    assert value.strip("'\"") == ""


@pytest.mark.parametrize(
    "page", ["/app/index.html", "/app/auth.html", "/app/change-password.html"]
)
def test_every_legacy_page_loads_the_config_before_its_app(client, page):
    html = client.get(page).text

    assert "./api-config.js" in html
    assert "./js/config.js" in html
    # Config has to be in place before the module that reads it runs.
    assert html.index("api-config.js") < html.index("js/config.js")


def test_modern_page_loads_the_config(client):
    html = client.get("/").text

    assert "/api-config.js" in html
    assert "/static/config.js" in html


def test_showcase_page_loads_the_config(client):
    """The demo tour logs in too, so it must not hard-code the API origin."""
    html = client.get("/showcase").text

    assert "/api-config.js" in html
    assert "/static/config.js" in html
    assert '"/api/v1/auth/login"' not in html


def test_wildcard_origins_drop_credentials(client):
    """``*`` with credentials is rejected by browsers, so the two are exclusive."""
    response = client.options(
        "/api/v1/auth/login",
        headers={
            "Origin": "https://msfactory.pages.dev",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "access-control-allow-credentials" not in response.headers


def test_cors_kwargs_never_combine_wildcard_and_credentials():
    """The browser rejects that pair, so building the middleware must not."""
    from app.main import cors_kwargs

    wildcard = cors_kwargs(["*"])
    assert wildcard["allow_credentials"] is False

    explicit = cors_kwargs(["https://msfactory.pages.dev"])
    assert explicit["allow_credentials"] is True
    assert explicit["allow_origins"] == ["https://msfactory.pages.dev"]


def test_explicit_origin_is_echoed_and_foreign_origins_are_refused():
    # Build a second app view with an explicit allowlist: the real app's CORS
    # settings are frozen at import time.
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.testclient import TestClient

    from app.main import cors_kwargs

    probe = FastAPI()
    probe.add_middleware(CORSMiddleware, **cors_kwargs(["https://msfactory.pages.dev"]))

    @probe.post("/x")
    def _x():
        return {}

    with TestClient(probe) as client:
        allowed = client.options(
            "/x",
            headers={"Origin": "https://msfactory.pages.dev", "Access-Control-Request-Method": "POST"},
        )
        refused = client.options(
            "/x", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://msfactory.pages.dev"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert refused.status_code == 400
    assert "access-control-allow-origin" not in refused.headers


def test_bootstrap_reads_sequentially_without_threads(monkeypatch):
    """Runtimes without threads must still produce the same payload shape."""
    seen: list[str] = []

    def fake_read(name, company_id, user_id):
        seen.append(name)
        if name == "snapshot":
            return {"items": [], "units": [], "warehouses": []}
        return [name]

    monkeypatch.setattr(bootstrap_module, "ThreadPoolExecutor", None)
    monkeypatch.setattr(bootstrap_module, "_read", fake_read)

    merged = bootstrap_module._read_all(["accounts", "snapshot", "team"], 1, 1)

    assert seen == ["accounts", "snapshot", "team"]
    assert sorted(merged) == ["accounts", "items", "team", "units", "warehouses"]


def test_bootstrap_fans_out_when_threads_exist(monkeypatch):
    seen: list[str] = []

    def fake_read(name, company_id, user_id):
        seen.append(name)
        if name == "snapshot":
            return {"items": [], "units": [], "warehouses": []}
        return [name]

    monkeypatch.setattr(bootstrap_module, "_read", fake_read)

    merged = bootstrap_module._read_all(["accounts", "snapshot", "team"], 1, 1)

    assert sorted(seen) == ["accounts", "snapshot", "team"]
    assert sorted(merged) == ["accounts", "items", "team", "units", "warehouses"]


def _request_with_headers(headers: dict[str, str]):
    from starlette.requests import Request

    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "headers": raw, "client": ("10.0.0.9", 1234)})


def test_forwarded_headers_are_ignored_by_default():
    """Without a trusted proxy in front, a client must not pick its own limit key."""
    from app.core.middleware import _client_ip

    request = _request_with_headers({"CF-Connecting-IP": "1.2.3.4"})

    assert _client_ip(request) == "10.0.0.9"


def test_forwarded_headers_are_used_when_trusted(monkeypatch):
    from app.core import middleware

    monkeypatch.setattr(middleware.settings, "trust_proxy_headers", True)

    assert middleware._client_ip(_request_with_headers({"CF-Connecting-IP": "1.2.3.4"})) == "1.2.3.4"
    assert middleware._client_ip(
        _request_with_headers({"X-Forwarded-For": "5.6.7.8, 10.0.0.1"})
    ) == "5.6.7.8"


def test_static_bundle_uses_paths_a_file_host_can_serve(tmp_path):
    """A file host has no /static prefix and no server-side substitution."""
    import subprocess

    root = Path(__file__).resolve().parents[1]
    dist = tmp_path / "dist"
    result = subprocess.run(
        ["bash", str(root / "scripts" / "build_static.sh")],
        capture_output=True,
        text=True,
        env={**os.environ, "DIST": str(dist)},
    )

    assert result.returncode == 0, result.stderr
    entry = (dist / "index.html").read_text()

    assert 'href="/static/' not in entry
    assert 'src="/static/' not in entry
    assert "__ASSET_VERSION__" not in entry
    assert (dist / "api-config.js").is_file()
    # One config file for both interfaces: the legacy pages climb to the root.
    assert 'src="../api-config.js"' in (dist / "app" / "index.html").read_text()
    assert not (dist / "app" / "api-config.js").exists()
