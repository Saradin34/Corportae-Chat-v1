#!/bin/sh
set -e
# Fix keytab permissions so MIT Kerberos accepts it (must be 600)
chmod 600 /etc/krb5.keytab 2>/dev/null || true

# Number of uvicorn workers. With >1 worker the WebSocket layer fans out
# through Redis pub/sub (see ws_manager.py), so a message sent by one worker
# reaches users connected to any other worker. Default 1 (safe everywhere).
WORKERS="${WEB_CONCURRENCY:-1}"

# Start the application
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
  --workers "${WORKERS}" \
  --proxy-headers --forwarded-allow-ips "*"
