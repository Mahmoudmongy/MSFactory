"""FastAPI application entrypoint.

Layering: api (HTTP) -> services (transactions + business rules) ->
repositories (tenant-scoped queries) -> db (models). Domain rules are pure and
live in ``app.domain``.
"""
import logging
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

app = FastAPI(
    title=f"{settings.app_name} API",
    version="1.0.0",
    description="نظام محاسبة متكامل متعدد الشركات — واجهة برمجية",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
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
ASSET_VERSION = "11"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
