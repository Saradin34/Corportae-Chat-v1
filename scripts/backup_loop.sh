#!/bin/sh
# ============================================================
#  Corporate Chat — scheduled PostgreSQL backup loop.
#  Runs inside the postgres:16-alpine image (has pg_dump + gzip).
#  Dumps to /backups, rotates to BACKUP_KEEP most-recent files.
# ============================================================
set -eu

INTERVAL="${BACKUP_INTERVAL:-86400}"   # seconds between backups (default 24h)
KEEP="${BACKUP_KEEP:-7}"               # how many dumps to retain

echo "[backup] starting. interval=${INTERVAL}s keep=${KEEP} db=${PGDATABASE} host=${PGHOST}"

while true; do
  TS="$(date +%Y%m%d_%H%M%S)"
  OUT="/backups/${PGDATABASE}_${TS}.sql.gz"
  echo "[backup] $(date -u +%FT%TZ) -> ${OUT}"

  if pg_dump --no-owner --no-privileges "${PGDATABASE}" | gzip -9 > "${OUT}.tmp"; then
    mv "${OUT}.tmp" "${OUT}"
    echo "[backup] OK ($(du -h "${OUT}" | cut -f1))"
  else
    echo "[backup] FAILED — will retry next cycle"
    rm -f "${OUT}.tmp" 2>/dev/null || true
  fi

  # Rotate: keep only the newest $KEEP dumps.
  COUNT="$(ls -1 /backups/${PGDATABASE}_*.sql.gz 2>/dev/null | wc -l || echo 0)"
  if [ "${COUNT}" -gt "${KEEP}" ]; then
    ls -1t /backups/${PGDATABASE}_*.sql.gz | tail -n +"$((KEEP + 1))" | while read -r old; do
      echo "[backup] rotating out ${old}"
      rm -f "${old}"
    done
  fi

  sleep "${INTERVAL}"
done
