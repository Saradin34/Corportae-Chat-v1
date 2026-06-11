#!/bin/sh
# ============================================================
#  Corporate Chat — restore the database from a backup dump.
#
#  Usage (run on the Docker host, from the project root):
#     ./scripts/restore_db.sh backups/corporate_chat_YYYYMMDD_HHMMSS.sql.gz
#
#  This DROPS and recreates the current data with the dump contents.
#  Stop the backend first so it doesn't write during restore:
#     docker compose stop backend
#     ./scripts/restore_db.sh <dump>
#     docker compose start backend
# ============================================================
set -eu

DUMP="${1:-}"
if [ -z "${DUMP}" ] || [ ! -f "${DUMP}" ]; then
  echo "Usage: $0 <path-to-backup.sql.gz>"
  echo "Available backups:"
  ls -1t backups/*.sql.gz 2>/dev/null || echo "  (none found in ./backups)"
  exit 1
fi

# Read DB creds from .env if present, else fall back to defaults.
PGUSER="$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d= -f2- || true)"
PGDATABASE="$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d= -f2- || true)"
PGUSER="${PGUSER:-chat}"
PGDATABASE="${PGDATABASE:-corporate_chat}"

echo "Restoring '${DUMP}' into database '${PGDATABASE}' (user ${PGUSER})…"
printf "This will OVERWRITE current data. Continue? [y/N] "
read -r ans
case "${ans}" in
  y|Y|yes|YES) ;;
  *) echo "Aborted."; exit 0 ;;
esac

# Pipe the gunzipped dump into psql inside the running db container.
gunzip -c "${DUMP}" | docker compose exec -T db psql -U "${PGUSER}" -d "${PGDATABASE}"

echo "Done. If the backend was stopped, start it: docker compose start backend"
