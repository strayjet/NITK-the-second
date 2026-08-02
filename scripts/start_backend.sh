#!/usr/bin/env bash
# Starts backend (citymind-backend / Event Aggregator) on port 9000.
# ml-service should already be running (see start_ml_service.sh) — backend
# will still start without it, but /aggregate/* calls will fail until it's up.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/backend"

if [ ! -d .venv ]; then
    echo "No .venv found in backend/. Run ./scripts/setup.sh first." >&2
    exit 1
fi

exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9000
