# citymind-yolo-service — computer vision microservice
FROM python:3.11-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Best-effort pre-fetch of the vehicle-detection checkpoint (yolov8n.pt).
# This is Ultralytics' stock COCO-pretrained model, publicly hosted on
# Ultralytics' GitHub releases and safe to auto-download/redistribute
# (AGPL-3.0, same license as the `ultralytics` package itself). It is
# baked into `weights/` here so the image is self-contained even if
# docker-compose's weights volume is ever removed.
#
# accident_best.pt is a project-specific fine-tuned checkpoint with no
# public hosting location this Dockerfile can reach — it is NOT
# auto-fetchable and must be supplied by the operator (see
# ml-service/README.md and docker-compose.yml's weights volume). If it's
# absent, the service still starts cleanly; /health reports
# accident_model_loaded=false and accident detection is simply disabled
# until the file is provided, exactly like any other missing-model case.
RUN mkdir -p weights \
    && (python -c "from ultralytics import YOLO; YOLO('weights/yolov8n.pt')" \
        || echo "WARNING: could not pre-fetch yolov8n.pt at build time (no network?) — \
it will be fetched at container startup instead if possible, or must be supplied via the weights volume.") \
    && chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8001

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

CMD ["./entrypoint.sh"]
