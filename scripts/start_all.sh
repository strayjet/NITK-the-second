#!/usr/bin/env bash
# Starts ml-service, backend, and the static frontend together.
# Ctrl+C stops all three cleanly.
# Run scripts/setup.sh first if you haven't already.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -d "$ROOT_DIR/ml-service/.venv" ] || [ ! -d "$ROOT_DIR/backend/.venv" ]; then
    echo "Virtualenvs not found. Run ./scripts/setup.sh first." >&2
    exit 1
fi

pids=()

cleanup() {
    echo
    echo "Stopping services..."
    for pid in "${pids[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

echo "Starting ml-service on http://localhost:8001 ..."
( cd "$ROOT_DIR/ml-service" && exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8001 ) &
pids+=($!)

# Give the CV models a few seconds' head start before backend's first
# health check against them — purely cosmetic, backend retries anyway.
sleep 3

echo "Starting backend on http://localhost:9000 ..."
( cd "$ROOT_DIR/backend" && exec ./.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 9000 ) &
pids+=($!)

echo "Starting frontend on http://localhost:8080 ..."
( cd "$ROOT_DIR/frontend" && exec python3 -m http.server 8080 --bind 0.0.0.0 ) &
pids+=($!)

echo
echo "All services running:"
echo "  ml-service: http://localhost:8001/docs"
echo "  backend:    http://localhost:9000/docs  (WebSocket: ws://localhost:9000/ws/live)"
echo "  frontend:   http://localhost:8080"
echo "Press Ctrl+C to stop all three."

wait
