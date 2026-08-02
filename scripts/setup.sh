#!/usr/bin/env bash
# One-time setup: creates a virtualenv for each service and installs its
# dependencies. Run this once before using start_all.sh.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Setting up ml-service =="
cd "$ROOT_DIR/ml-service"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip --quiet
./.venv/bin/pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "ml-service ready (venv at ml-service/.venv)"

echo
echo "== Setting up backend =="
cd "$ROOT_DIR/backend"
python3 -m venv .venv
./.venv/bin/pip install --upgrade pip --quiet
./.venv/bin/pip install -r requirements.txt
[ -f .env ] || cp .env.example .env
echo "backend ready (venv at backend/.venv)"

echo
echo "== Backend database migrations =="
echo "Requires a running PostgreSQL reachable at DATABASE_URL in backend/.env"
echo "(see docker-compose.yml for a one-command Postgres, or run your own)."
if ./.venv/bin/alembic upgrade head; then
    echo "migrations applied"
else
    echo "WARNING: migrations failed — check DATABASE_URL in backend/.env and that PostgreSQL is running." >&2
fi

echo
echo "Setup complete. Next: ./scripts/start_all.sh"
