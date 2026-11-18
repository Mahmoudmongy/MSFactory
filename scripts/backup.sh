#!/usr/bin/env bash
# Back up the OpenBooks database to a compressed, timestamped dump.
#
# Usage:  ./scripts/backup.sh [output-dir]
# Cron:   0 2 * * *  /app/scripts/backup.sh /backups
#
# The dump uses --clean so restoring into an existing database replaces objects
# instead of failing on conflicts.
set -euo pipefail

OUTPUT_DIR="${1:-./backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DATABASE_URL="${OPENBOOKS_DATABASE_URL:-postgresql+psycopg://openbooks:openbooks_dev_pw@127.0.0.1:5432/openbooks}"

# Convert the SQLAlchemy URL into the libpq form pg_dump expects.
PG_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

mkdir -p "$OUTPUT_DIR"
FILE="${OUTPUT_DIR}/openbooks_${TIMESTAMP}.sql.gz"

echo "Backing up to ${FILE}"
pg_dump --clean --if-exists --no-owner --no-privileges "$PG_URL" | gzip -9 > "$FILE"

# Keep the last 30 dumps.
find "$OUTPUT_DIR" -name 'openbooks_*.sql.gz' -type f -printf '%T@ %p\n' \
    | sort -rn | tail -n +31 | cut -d' ' -f2- | xargs -r rm -f

echo "Done: $(du -h "$FILE" | cut -f1)"
