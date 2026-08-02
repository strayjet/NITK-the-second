# CityMind — Full System (Phases A–D)

CityMind is a smart-city platform: computer-vision incident detection,
event aggregation + persistence, city routing/planning logic, an AI
copilot, a heatmap engine, a scenario simulator, real-time WebSocket push,
and a live dashboard frontend — wired together end to end.

```
┌──────────────┐  HTTP (detect/*)   ┌───────────────────────────────────────┐
│  ml-service   │ ◄───────────────  │               backend                  │
│ (YOLO + accid.│  DetectionEvents  │  Event Aggregator → Incidents (Postgres)│
│  + emergency)  │ ─────────────────► City Logic (routing/closures/facilities)│
└──────────────┘                    │  Dashboard API · Heatmap · Copilot     │
                                     │  Scenario Simulator · /ws/live (WS)    │
                                     └───────────────┬───────────────────────┘
                                                      │ REST + WebSocket
                                             ┌────────▼─────────┐
                                             │     frontend       │
                                             │ static dashboard    │
                                             │ (map, live feed,     │
                                             │  copilot chat, etc.)  │
                                             └────────────────────┘
```

## Run everything with one command

```bash
docker compose up --build
```

This starts, in order: `postgres`, `redis`, `ml-service`, `backend`
(runs Alembic migrations automatically on boot), and `frontend`.

| Service | URL |
|---|---|
| Frontend dashboard | http://localhost:8080 |
| Backend API docs (Swagger) | http://localhost:9000/docs |
| Backend WebSocket | ws://localhost:9000/ws/live |
| ml-service API docs | http://localhost:8001/docs |
| Postgres | localhost:5432 (`citymind`/`citymind`) |
| Redis | localhost:6379 |

`city_logic` (routing/closures/facility recommendation, and the `/heatmap`
population layer) is disabled by default in Docker (`CITY_LOGIC_ENABLED=false`)
because it needs a real WorldPop `.tif` raster mounted into the container —
see the commented-out `volumes:` block in `docker-compose.yml` for how to
enable it. Everything else (detection, aggregation, incidents, dashboard,
accident/closure heatmap layers, copilot, scenario simulator, real-time
push) works with zero extra configuration.

### Troubleshooting

- **`ml-service` restart-looping with `PermissionError: config/camera_map.json`**
  (common on Fedora/RHEL and any SELinux-enforcing host): the bind-mounted
  `ml-service/weights` and `ml-service/config` volumes need an SELinux
  relabel. This is already handled in `docker-compose.yml` via the `:ro,z`
  volume flag — if you still hit it, confirm you're on the version of
  `docker-compose.yml` that has `:ro,z` (not just `:ro`) on those two mounts.
- **`backend` restart-looping with `ImportError: libexpat.so.1: cannot open
  shared object file`**: the `python:3.11-slim` base image doesn't ship
  `libexpat1`, which `rasterio`'s bundled GDAL needs at runtime. Already
  fixed in `backend/Dockerfile` (installs `libexpat1`) — if you built an
  image before this fix, run `docker compose build --no-cache backend`.
- **`permission denied while trying to connect to the docker API`**: your
  user isn't in the `docker` group. Run
  `sudo usermod -aG docker $USER`, then log out/in (or `newgrp docker` in
  your current shell) and retry.
- Check container status/logs any time with `docker compose ps` and
  `docker logs <container-name> --tail 50` (names are `citymind-backend`,
  `citymind-ml-service`, `citymind-frontend`, `citymind-postgres`,
  `citymind-redis`).

### Running without Docker

```bash
./scripts/setup.sh       # creates venvs, installs deps, runs migrations
./scripts/start_all.sh   # starts ml-service (8001), backend (9000), frontend (8080)
```

You'll need a Postgres reachable at `backend/.env`'s `DATABASE_URL` (the
`docker compose up postgres redis` services work fine for this even if you
run the apps themselves outside Docker). Redis is optional outside Docker —
`backend` runs in single-process WebSocket mode without it.

## What's new in this pass (Phase D): real-time + frontend

The system already had solid, fully-implemented services for detection,
aggregation, persistence, city logic, the dashboard API, the AI copilot, the
heatmap engine, and the scenario simulator (see "Phase 1–3" history below —
none of that needed rewriting). What was missing, and what this pass added:

1. **Real-time push (`app/services/realtime.py`, `app/api/ws_routes.py`).**
   A `RealtimeHub` fans out incident/dashboard events to every client
   connected to `GET /ws/live`, as JSON frames `{"type": ..., "data": ...}`.
   Single-process by default (no extra infra needed); set `REDIS_URL` to fan
   out across multiple backend replicas via Redis Pub/Sub. Wired into
   `/aggregate/image`, `/aggregate/video`, `/aggregate/stream`,
   `/city/route`, `/city/simulate-closure`, and `/city/recommend-facility`.
2. **Optional continuous camera polling (`app/services/live_monitor.py`).**
   Configure real RTSP/HTTP camera URLs via the `LIVE_CAMERA_SOURCES` env
   var (JSON array of `{camera_id, source_url, poll_interval_seconds}`) and
   the backend will continuously poll each one through `ml-service`,
   aggregate, persist, and broadcast — with zero manual uploads. Left empty
   by default; no synthetic/mock data is ever generated.
3. **`frontend/` — a dependency-free static dashboard** (plain HTML/CSS/JS,
   served by nginx in Docker or `python -m http.server` locally): dashboard
   summary cards, a live incident feed over `/ws/live`, a Leaflet map with
   the population/accidents/closures heatmap layers, an image-upload
   detection tester, a scenario-simulator form, and a copilot chat box. The
   API base URL is editable at runtime (top bar) and persisted to
   `localStorage`, so the same static build works against any backend host.
4. **`docker-compose.yml`** now includes `redis` and `frontend` services
   and wires `REDIS_URL`/`CORS_ALLOW_ORIGINS`/`LIVE_CAMERA_SOURCES` into
   `backend`'s environment.

---

# CityMind

CityMind turns camera feeds into structured traffic-incident data. This
repo currently implements the first two layers of that pipeline, connected
and runnable end to end:

```
┌─────────────────┐  HTTP   ┌───────────────────┐
│   ml-service      │◄───────│      backend        │
│  (YOLO detection)  │ dets. │ (Event Aggregator)    │  ──▶  (future layers)
└─────────────────┘         └───────────────────┘
   camera frame in            incident-level JSON out
```

| Layer | Folder | What it does |
|---|---|---|
| 1. YOLO Detection Service | `ml-service/` | Runs vehicle detection/tracking, accident detection, and emergency-vehicle detection on an image, video, webcam, or live stream. Returns one standardized `DetectionEvent` per processed frame. Pure computer vision — no memory of past frames beyond a single request, no opinion about what an "incident" is. |
| 2. Event Aggregator | `backend/` | Calls `ml-service` over HTTP, and turns its frame-level events into incident-level events — e.g. 40 consecutive "accident" frames on one camera become a single `Incident` with a start time, end time, peak confidence, and whether an emergency vehicle was involved. |
| 3+. Traffic Simulation, Infrastructure Planning, AI Copilot, React Dashboard | *(not yet built)* | Future layers that will consume `backend`'s `Incident` data. |

---

## Phase 1 — What's here, what isn't (as found)

**`ml-service` was already complete and well-built.** Vehicle
detection+tracking (YOLOv8 + ByteTrack), accident detection (dedicated model
gated by a motion + collision heuristic so single noisy frames don't false-positive),
and an emergency-vehicle heuristic (class + ambulance-livery color check) were
all implemented, tested-shape, and exposed via 5 clean FastAPI endpoints
(`/health`, `/detect/image`, `/detect/video`, `/detect/webcam`,
`/detect/stream`). Nothing in this layer needed building — it does exactly
one job and does it well.

**`backend` (the Event Aggregator) already existed as a separate project**
with a resilient HTTP client for `ml-service`, a stateful frame→incident
aggregator, and 4 endpoints (`/health`, `/aggregate/image`,
`/aggregate/video`, `/aggregate/stream`). Its logic was sound, but it had
never actually been run alongside `ml-service`.

**What was actually disconnected, found and fixed in this pass:**

1. **Port mismatch (the real integration bug).** `ml-service` listens on
   port `8001` by default. `backend`'s default `YOLO_SERVICE_BASE_URL` was
   `http://localhost:8000` — a port nothing was listening on. Two
   individually-correct services that would have failed to talk to each
   other the moment you ran them together. **Fixed**: `backend`'s default
   now points at `http://localhost:8001`, confirmed working with zero
   `.env` configuration (see Phase 3 below).
2. **No shared way to run both services.** Each project had its own
   `requirements.txt` and README, but there was no single command to bring
   the system up. **Fixed**: added `scripts/setup.sh` and
   `scripts/start_all.sh` (details below).
3. **No top-level structure or docs connecting the two projects.** They
   were two sibling folders with no explanation of how they relate.
   **Fixed**: this README, `shared/` (wire-contract docs), and `configs/`
   (a single place to see how the two services' ports must agree).
4. **No input validation on `backend`'s upload endpoints.** A wrong file
   type would be forwarded all the way to `ml-service` before failing.
   **Fixed**: lightweight content-type checks now reject obviously-wrong
   uploads immediately with a clear `400`.

Everything else was already solid and was left alone — see
`backend/README.md` and `ml-service/README.md` for each service's own
design notes.

---

## Phase 2 — Architecture

```
Client
  │
  │  POST /aggregate/video  (multipart file upload)
  ▼
backend  (FastAPI, port 9000)
  │  - validates upload (content-type, non-empty)
  │  - YoloClient: forwards file to ml-service over HTTP,
  │    with retries (connection/timeout/5xx) and structured logging
  ▼
ml-service  (FastAPI, port 8001)
  │  - decodes frames, runs YOLOv8 detection + ByteTrack
  │  - runs accident detection (model + motion/collision gate)
  │  - runs emergency-vehicle heuristic
  │  - returns one DetectionEvent per frame
  ▲
  │  JSON: { frames_processed, events: [DetectionEvent, ...] }
  │
backend
  │  - EventAggregator: feeds every DetectionEvent through a stateful,
  │    per-camera state machine (confidence threshold, cooldown gap,
  │    minimum duration) to collapse many frames into deduplicated
  │    Incidents, closing any incident still open at the end of this
  │    bounded request
  ▼
Client
     JSON: AggregationResult { incidents: [...], active_incidents: [...] }
```

**Communication**: plain synchronous REST over HTTP (`httpx.AsyncClient`),
not a message queue — deliberately, because this is a request/response
pipeline (one uploaded file in, one aggregated result out), not a
fire-and-forget event stream. If a live, always-on camera feed is added
later, that's the point where a queue (e.g. Redis Streams / Kafka) would
start to make sense — not needed for this MVP.

**Where inference happens**: exclusively inside `ml-service`. `backend`
never touches a pixel, a model, or GPU/CPU inference — it only calls HTTP
endpoints and does bookkeeping on the JSON that comes back.

**Where results are stored**: nowhere yet, by design. `EventAggregator` keeps
active-incident state in memory (per camera, guarded by an `asyncio.Lock`)
for as long as the process runs, and returns closed incidents in the API
response. There is no database in this MVP — see the Roadmap for when to add
one.

---

## Phase 3 — Running it (step by step)

### Requirements
- Python 3.10+
- ~500MB free disk for `ml-service`'s dependencies (PyTorch + Ultralytics)

### 1. One-time setup

```bash
cd CityMind
./scripts/setup.sh
```

This creates a virtualenv for each service (`ml-service/.venv`,
`backend/.venv`), installs each one's `requirements.txt`, and copies each
service's `.env.example` to `.env` if you haven't already.

### 2. Run both services

```bash
./scripts/start_all.sh
```

This starts `ml-service` on `http://localhost:8001` and `backend` on
`http://localhost:9000`, and stops both cleanly on Ctrl+C. Or run them in
separate terminals if you'd rather see each service's logs on its own:

```bash
./scripts/start_ml_service.sh   # terminal 1
./scripts/start_backend.sh      # terminal 2
```

### 3. Verify the connection

```bash
curl http://localhost:9000/health
```

```json
{
  "status": "ok",
  "service": "citymind-backend",
  "yolo_service_reachable": true,
  "yolo_service": { "status": "ok", "service": "citymind-yolo-service", "yolo_model_loaded": true, "accident_model_loaded": true },
  "yolo_service_error": null
}
```

If `yolo_service_reachable` is `false`, `ml-service` isn't running yet, or
`backend/.env`'s `YOLO_SERVICE_BASE_URL` doesn't match wherever `ml-service`
is actually listening (see `configs/ports.reference.env`).

### 4. Example request

```bash
curl -F "file=@/path/to/traffic_clip.mp4" \
     "http://localhost:9000/aggregate/video?camera_id=CAM_04"
```

```json
{
  "camera_id": "CAM_04",
  "road_id": "MG_ROAD",
  "frames_processed": 45,
  "incidents": [
    {
      "incident_id": "b1e6b5b0-...",
      "status": "CLOSED",
      "camera_id": "CAM_04",
      "start_time": "2026-08-01T10:03:12+00:00",
      "end_time": "2026-08-01T10:03:41+00:00",
      "duration_seconds": 29.0,
      "frame_count": 14,
      "max_confidence": 0.94,
      "emergency_vehicle_present": true,
      "peak_vehicle_count": 6,
      "traffic_density_at_peak": "HIGH"
    }
  ],
  "active_incidents": [],
  "incident_count": 1,
  "active_incident_count": 0
}
```

For images: `curl -F "file=@photo.jpg" "http://localhost:9000/aggregate/image?camera_id=CAM_04"`.
For live streams: `POST http://localhost:9000/aggregate/stream` with JSON body
`{"source_url": "rtsp://...", "camera_id": "CAM_04"}`.

Interactive API docs (try requests from the browser): `http://localhost:9000/docs`
and `http://localhost:8001/docs`.

---

## Project structure

```
CityMind/
    backend/              Event Aggregator (Layer 2) — see backend/README.md
    ml-service/           YOLO detection microservice (Layer 1) — see ml-service/README.md
    shared/               Wire-contract documentation shared by both services (not imported code)
    configs/              Quick-reference for how the two services' ports must line up
    scripts/
        setup.sh            One-time: create venvs, install deps, seed .env files
        start_ml_service.sh   Run only ml-service
        start_backend.sh      Run only backend
        start_all.sh           Run both together, Ctrl+C stops both
    README.md             This file
```

---

## Phase 6 — What's already in place for MVP-readiness

- **Logging**: both services log every request, retry, and failure via
  Python's `logging` module (`ml-service`'s model loads and per-request
  pipeline steps; `backend`'s outbound HTTP calls, retries, and incident
  open/close events).
- **Health checks**: `GET /health` on both services; `backend`'s also
  reports whether it can currently reach `ml-service`.
- **Validation**: `ml-service` rejects undecodable images/unopenable video
  sources with a `400`; `backend` now rejects uploads with the wrong
  content-type before forwarding them.
- **Clear API responses**: consistent JSON error shapes (`{"detail": "..."}`)
  and typed, documented response models (visible in each service's
  `/docs`).

## Phase 7 — Roadmap

**Do next (concrete, in order):**
1. Run `./scripts/setup.sh && ./scripts/start_all.sh` and hit `/aggregate/image`
   with one real photo and `/aggregate/video` with one real clip — confirm the
   accident/emergency-vehicle detection quality on your actual footage before
   building anything else on top.
2. Tune `backend/.env`'s `AGGREGATOR_CONFIDENCE_THRESHOLD`,
   `AGGREGATOR_COOLDOWN_SECONDS`, and `AGGREGATOR_MIN_DURATION_SECONDS`
   against real footage — these three numbers are what separates "one real
   incident" from "ten fragmented ones" or "a false alarm," and the right
   values depend on your cameras' frame rate and scene.
3. Fill in `ml-service/config/camera_map.json` with your real camera IDs,
   roads, and coordinates so incidents come back geolocated.

**To reach a fuller MVP after that:**
4. Persist incidents. Right now `EventAggregator`'s state lives in memory
   and disappears on restart. Add a small SQLite (or Postgres, if you're
   already comfortable with it) table for closed incidents — this is the
   natural next layer before "Traffic Simulation" can consume anything
   durable.
5. Add a way to feed a live stream continuously rather than one bounded
   request at a time — e.g. a background task that polls
   `/aggregate/stream` on an interval per camera, or has `ml-service` push
   frames instead of `backend` pulling them.
6. Start the Traffic Simulation layer as its own service, consuming
   `backend`'s `Incident` model (documented in `shared/detection_event.schema.json`'s
   sibling contract, once you add one for `Incident`) rather than raw
   detections.

**For production, later still:**
7. Auth (even a simple API key) on both services' public endpoints.
8. Move `ml-service` inference off the request thread — a queue + worker
   (Celery/RQ, or just a background task) if video processing time becomes
   a bottleneck under load.
9. Containerize each service independently (they already don't share code,
   so this is mostly writing two Dockerfiles) once you know their real
   resource needs (GPU for `ml-service`, CPU-only for `backend`).
10. Structured, centralized logging (e.g. ship both services' logs
    somewhere queryable) once there's more than one developer debugging
    this.

## Assumptions made in this pass

- `ml-service`'s port (`8001`) was treated as the fixed point and
  `backend`'s default was corrected to match it, rather than the reverse,
  since `ml-service`'s own README and `.env.example` already documented
  `8001` consistently.
- `backend`'s port was left at `9000` (arbitrary but unused, and clearly
  distinct from `8001` to avoid confusion).
- No database was added — the brief asked to keep things simple and
  MVP-focused, and persistence is called out explicitly in the Roadmap
  instead of guessed at.
- Content-type validation on uploads is intentionally shallow (checks the
  `Content-Type` header, not the file's actual bytes) — good enough to
  catch obvious mistakes for a solo developer's own testing, not a
  security control.
