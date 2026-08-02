#!/bin/sh
# citymind-backend container entrypoint.
#
# Order matters and every step here is deliberately non-fatal where a
# fresh/offline environment could otherwise block startup entirely:
#
#   1. fetch_worldpop.py  — best-effort: ensures a valid GeoTIFF exists at
#      CITY_LOGIC_WORLDPOP_TIF_PATH. Never exits non-zero; if it can't get a
#      valid raster (no network, blocked DNS, etc.) city_logic simply stays
#      not-ready and /city/* returns 503 (see app/services/city_service.py),
#      exactly as it already did for any other initialization failure.
#   2. alembic upgrade head — required: the schema must be current before
#      the app serves traffic. This already retries safely (no-op if
#      current) but if Postgres is genuinely unreachable we DO want the
#      container to fail loudly (and restart, per `restart: unless-stopped`)
#      rather than silently serve against a stale/missing schema.
#   3. uvicorn — the actual app.
set -e

echo "[entrypoint] ensuring WorldPop population raster is available..."
python scripts/fetch_worldpop.py || true

echo "[entrypoint] applying database migrations..."
alembic upgrade head

echo "[entrypoint] starting uvicorn..."
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-9000}"
