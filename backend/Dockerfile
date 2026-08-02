# citymind-backend — Event Aggregator + City Logic + Dashboard API
FROM python:3.11-slim AS base

# System deps: build tooling for any sdists in the geo stack (rasterio,
# geopandas, shapely, osmnx, scipy all ship manylinux wheels for the pinned
# versions below, but build-essential is kept as a fallback for platforms
# without a matching wheel); libexpat1 is a runtime dependency of rasterio's
# bundled GDAL that the slim base image doesn't include by default; curl is
# for the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libexpat1 \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x entrypoint.sh \
    && mkdir -p /app/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=9000

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:9000/health || exit 1

# entrypoint.sh: best-effort WorldPop raster fetch (never fails startup),
# then required Alembic migrations, then the API. See entrypoint.sh for the
# rationale on why each step is fatal or non-fatal.
CMD ["./entrypoint.sh"]
