# Pinned to Python 3.11 — stable wheels for all deps.
# This is fully isolated from whatever Python you have locally
# (e.g. a 3.14 preview), so there are NO Python/JS version conflicts.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# System deps (libpq for asyncpg build is not needed for the wheel, but
# keep build tools minimal). curl for the healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends curl libkrb5-dev krb5-user gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY app ./app
COPY krb5.conf /etc/krb5.conf
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

EXPOSE 8000

# Healthcheck used by docker-compose so nginx waits for a ready backend.
# start_period is generous because the backend retries the DB connection
# on first boot (Postgres init race) before it starts serving.
HEALTHCHECK --interval=5s --timeout=3s --retries=30 --start-period=75s \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]
