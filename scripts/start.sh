#!/bin/sh
# Container entrypoint.
#
# Migrations run at start rather than in a platform pre-deploy hook because
# Cloudflare Containers has no such hook: the image is the only unit of
# deployment. Two guards keep concurrent starts safe. A retry loop waits for a
# remote Postgres to accept connections, and a session-level advisory lock held
# on its own connection makes a second starter queue instead of racing on
# alembic_version.
set -e

python - <<'PY'
import subprocess
import sys
import time

from sqlalchemy import text

from app.db.session import engine

LOCK_KEY = 728_914_001
DEADLINE_SECONDS = 120
started = time.monotonic()

# Hold the lock on a dedicated connection for the whole migration. A
# transaction-scoped lock would be released before Alembic ran.
#
# connect() itself dials the database, so it has to sit inside the retry:
# outside, an unreachable host kills the container on the first attempt
# instead of waiting for Postgres to come up.
conn = None
while True:
    try:
        conn = engine.connect()
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        break
    except Exception as exc:  # noqa: BLE001 - every failure here is retryable
        if conn is not None:
            conn.close()
            conn = None
        if time.monotonic() - started > DEADLINE_SECONDS:
            raise SystemExit(f"database never became reachable: {exc}") from exc
        print(f"waiting for database: {exc}", flush=True)
        engine.dispose()
        time.sleep(2)

try:

    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"])
    if result.returncode != 0:
        raise SystemExit(result.returncode)
finally:
    try:
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
        conn.commit()
    finally:
        conn.close()
PY

exec python -m uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
