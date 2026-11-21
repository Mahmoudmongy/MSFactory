FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY legacy ./legacy
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts/start.sh ./scripts/start.sh

# Run as an unprivileged user.
RUN chmod +x ./scripts/start.sh \
    && useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT:-8000}/health" || exit 1

# The start script runs migrations, then execs uvicorn. Cloudflare Containers
# has no pre-deploy hook, so this is the only place migrations can run; the
# script also holds an advisory lock so two cold starts cannot race.
# PORT stays overridable: Railway and Cloudflare both inject it, and the
# default keeps `docker run -p 8000:8000` working.
CMD ["sh", "-c", "exec sh ./scripts/start.sh"]
