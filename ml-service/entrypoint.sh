#!/bin/sh
# citymind-yolo-service container entrypoint.
#
# The image's build step already tries to pre-fetch weights/yolov8n.pt (see
# Dockerfile). This is a second, runtime-time safety net for the case where
# the weights/ directory is volume-mounted over whatever the image baked in
# (see docker-compose.yml) and arrives empty on a fresh clone: if
# yolov8n.pt is still missing, try once more, now that the container is
# actually running (and may have network even if the build environment
# didn't). Never fatal — app/main.py's lifespan already loads models
# defensively and /health reports yolo_model_loaded=false /
# accident_model_loaded=false rather than crashing if a checkpoint is
# missing or fails to load.
set -e

if [ ! -f "weights/yolov8n.pt" ]; then
    echo "[entrypoint] weights/yolov8n.pt not found — attempting fetch..."
    python -c "from ultralytics import YOLO; YOLO('weights/yolov8n.pt')" \
        || echo "[entrypoint] fetch failed (no network?) — vehicle detection will report yolo_model_loaded=false until weights/yolov8n.pt is provided."
fi

if [ ! -f "weights/accident_best.pt" ]; then
    echo "[entrypoint] NOTE: weights/accident_best.pt not found. This is a project-specific" \
         "fine-tuned checkpoint with no public download source — it must be supplied via" \
         "the weights volume. Accident detection will report accident_model_loaded=false" \
         "until it is provided."
fi

echo "[entrypoint] starting uvicorn..."
exec uvicorn app.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8001}"
