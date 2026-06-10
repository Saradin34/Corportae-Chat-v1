#!/bin/sh
set -e
# Fix keytab permissions so MIT Kerberos accepts it (must be 600)
chmod 600 /etc/krb5.keytab 2>/dev/null || true
# Start the application
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips "*"
