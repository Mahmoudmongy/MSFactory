"""FastAPI application entrypoint.

Layering: api (HTTP) -> services (transactions + business rules) ->
repositories (tenant-scoped queries) -> db (models). Domain rules are pure and
live in ``app.domain``.
"""
import json
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from .api.v1.router import api_router
from .core.config import settings
from .core.errors import AppError
from .core.middleware import RateLimitMiddleware
from .db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("msfactory")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
# The legacy screen set lives beside the modern app and is served on its own
# path. It is a separate, self-contained interface: it has its own service
# worker scope, its own assets and its own bootstrap, so the two never share
# state or caches.
LEGACY_DIR = Path(__file__).resolve().parent.parent / "legacy"

app = FastAPI(
    title=f"{settings.app_name} API",
    version="1.0.0",
    description="نظام محاسبة متكامل متعدد الشركات — واجهة برمجية",
)

# Browsers reject ``Access-Control-Allow-Origin: *`` on credentialed requests,
# so the two cannot be combined. This app authenticates with a bearer token
# rather than a cookie, so a wildcard is safe as long as credentials are off;
# once the frontend is hosted separately and origins are listed explicitly,
# credentials can be on.


def cors_kwargs(origins: list[str]) -> dict:
    return {
        "allow_origins": origins,
        "allow_credentials": "*" not in origins,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
        "expose_headers": ["Content-Disposition"],
    }


app.add_middleware(CORSMiddleware, **cors_kwargs(settings.cors_origin_list))
app.add_middleware(RateLimitMiddleware)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


@app.get("/health", tags=["system"])
def health():
    database_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # pragma: no cover - depends on live infra
        database_ok = False
    return {"status": "ok" if database_ok else "degraded", "database": database_ok, "version": app.version}


app.include_router(api_router)

# One place to bump when the frontend changes: index.html references
# /static/app.js?v=<ASSET_VERSION>, so browsers and the service worker always
# pick up the new files while still caching aggressively in between releases.
ASSET_VERSION = "22"


@app.get("/api-config.js", include_in_schema=False)
def api_config():
    """The one file a deployment edits to point the frontends at a backend.

    Both interfaces load it from the site root, so it is served here rather
    than mounted under either app's directory.
    """
    return FileResponse(
        LEGACY_DIR / "api-config.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


class NoHeuristicCacheStatic(StaticFiles):
    """Static files that browsers must revalidate instead of caching blindly.

    Starlette sends only ``ETag``/``Last-Modified``, so a browser may reuse a
    file for an arbitrary heuristic lifetime. During development that served
    stale JavaScript after a deploy and the fixes appeared not to work.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


if STATIC_DIR.exists():
    app.mount("/static", NoHeuristicCacheStatic(directory=STATIC_DIR), name="static")

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        """Served from the root so the worker's scope covers the whole app."""
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/", include_in_schema=False)
    def index():
        html = (STATIC_DIR / "index.html").read_text().replace("__ASSET_VERSION__", ASSET_VERSION)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

    @app.get("/showcase", include_in_schema=False)
    def showcase():
        """Live demo walkthrough: the real app in a phone frame, screen by screen.

        Demo credentials come from the environment so they are never committed;
        without them the page still renders as a static tour of the screens.
        """
        html = (STATIC_DIR / "showcase.html").read_text()
        demo = {
            "email": os.environ.get("MSFACTORY_DEMO_EMAIL", ""),
            "password": os.environ.get("MSFACTORY_DEMO_PASSWORD", ""),
        }
        injection = f"<script>window.__MSFACTORY_DEMO__={json.dumps(demo)};</script>"
        html = html.replace("</head>", f"{injection}\n</head>")
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})

if LEGACY_DIR.exists():
    # ``html=True`` serves index.html for directory requests and keeps the
    # legacy service worker scoped to /app, so it can never intercept the
    # modern app's requests.
    app.mount("/app", NoHeuristicCacheStatic(directory=LEGACY_DIR, html=True), name="legacy")

    preview_dir = Path(__file__).resolve().parent.parent / "preview"
    if preview_dir.is_dir():
        app.mount("/preview", NoHeuristicCacheStatic(directory=preview_dir, html=True), name="preview")
