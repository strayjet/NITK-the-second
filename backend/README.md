# citymind-backend

The **Event Aggregator** layer of CityMind.

```
YOLO Detection Service   (citymind-yolo-service, separate project, unmodified)
        ↓
Event Aggregator         (this project — citymind-backend)
        ↓
Traffic Simulation        (future layer, not implemented here)
        ↓
Infrastructure Planning    (future layer, not implemented here)
        ↓
AI Copilot                 (future layer, not implemented here)
        ↓
React Dashboard             (future layer, not implemented here)
```

This service is a pure client of `citymind-yolo-service` — it calls that
service over HTTP and never imports or modifies its code. Its one job is to
turn many frame-level `DetectionEvent`s into deduplicated, incident-level
`Incident`s (e.g. 40 consecutive "accident" frames on camera `CAM_04`
becoming a single incident with a start time, an end time, and summary
stats), and to expose that as a small HTTP API for the layers above it.

## Project layout

```
citymind-backend/
    app/
        main.py                  FastAPI app, lifespan-managed singletons
        config.py                Centralized, env-driven settings
        api/
            routes.py             /health, /aggregate/image|video|stream
        services/
            yolo_client.py         Resilient async HTTP client for citymind-yolo-service
            event_aggregator.py     Frame-level events -> incident-level events
        models/
            detection.py            Wire-format mirror of the YOLO service's schemas
            incident.py              Incident / AggregationResult response models
        simulation/                Reserved for the next CityMind layer (empty)
        utils/
            time_utils.py            ISO-8601 timestamp parsing helpers
    requirements.txt
    .env.example
    README.md
```

## Requirements

- Python 3.11+
- A running instance of `citymind-yolo-service` / `ml-service` (this
  backend defaults to `http://localhost:8001`, matching that service's own
  default port — set `YOLO_SERVICE_BASE_URL` if you've reconfigured either
  one).

## Setup

```bash
cd citymind-backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env as needed
```

## Run

```bash
uvicorn app.main:app --reload
```

By default the API listens on `http://localhost:9000` (change via `PORT`/
`HOST` in `.env`; `--reload` uses whatever `--host`/`--port` you pass to
uvicorn directly, e.g. `uvicorn app.main:app --reload --host 0.0.0.0 --port 9000`).

Interactive API docs: `http://localhost:9000/docs`

## Configuration

All settings live in `app/config.py` and are overridable via environment
variables or a `.env` file (see `.env.example` for the full list):

| Variable | Default | Meaning |
|---|---|---|
| `YOLO_SERVICE_BASE_URL` | `http://localhost:8001` | Where `citymind-yolo-service` / `ml-service` is running |
| `YOLO_SERVICE_MAX_RETRIES` | `3` | Retry attempts for transient connection/5xx failures |
| `AGGREGATOR_CONFIDENCE_THRESHOLD` | `0.6` | Minimum confidence for a frame to count as an accident candidate |
| `AGGREGATOR_COOLDOWN_SECONDS` | `5.0` | Gap (by event timestamp) tolerated before an active incident is closed |
| `AGGREGATOR_MIN_DURATION_SECONDS` | `1.0` | Incidents shorter than this are discarded as noise |

## API

### `GET /health`

Backend liveness plus a best-effort reachability check of the upstream YOLO
service. Never throws just because the YOLO service is down — reports
`"status": "degraded"` instead so callers can distinguish "backend is up but
YOLO is unreachable" from "backend itself is broken".

### `POST /aggregate/image`

Multipart upload. Fields:

- `file` — the image (required)
- `camera_id` — query parameter (optional)

Forwards the image to `citymind-yolo-service`'s `/detect/image`, feeds the
resulting single frame through the Event Aggregator, and returns an
`AggregationResult`.

### `POST /aggregate/video`

Multipart upload. Fields:

- `file` — the video (required)
- `camera_id` — query parameter (optional)
- `max_frames` — query parameter (optional)

Forwards the video to `citymind-yolo-service`'s `/detect/video`, feeds every
returned frame through the Event Aggregator in order, and — because a video
file is a bounded unit of work — force-closes any incident still active for
that camera at the end, so incidents don't stay open forever waiting for a
cooldown gap that will never come within this request.

### `POST /aggregate/stream`

JSON body:

```json
{
  "source_url": "rtsp://example.com/live",
  "camera_id": "CAM_04",
  "max_frames": 150
}
```

Forwards the stream URL to `citymind-yolo-service`'s `/detect/stream` (which
opens it with OpenCV — anything `cv2.VideoCapture` can read: RTSP, HTTP
MJPEG, etc.), aggregates the returned frames the same way `/aggregate/video`
does, and force-closes any incident still active at the end of this
request's frame batch.

### Response shape (`AggregationResult`)

```json
{
  "camera_id": "CAM_04",
  "road_id": "ROAD_12",
  "frames_processed": 150,
  "incidents": [
    {
      "incident_id": "b1e6...",
      "incident_type": "ACCIDENT",
      "status": "CLOSED",
      "camera_id": "CAM_04",
      "road_id": "ROAD_12",
      "start_time": "2026-08-01T10:03:12+00:00",
      "last_seen_time": "2026-08-01T10:03:41+00:00",
      "end_time": "2026-08-01T10:03:41+00:00",
      "duration_seconds": 29.0,
      "frame_count": 14,
      "max_confidence": 0.94,
      "avg_confidence": 0.81,
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

## Design notes

- **Statefulness**: the `EventAggregator` is instantiated once at app
  startup (`app/main.py`) and shared across requests via
  `app.state.event_aggregator`, guarded by an `asyncio.Lock`. This is
  deliberate: real incidents legitimately span multiple HTTP calls (e.g.
  successive polling intervals against a live stream), so aggregation state
  must outlive any single request.
- **Duplicate prevention**: at most one active incident is tracked per
  `camera_id` at a time — a new accident-candidate frame on a camera that
  already has an active incident extends that incident rather than opening
  a second one.
- **Resilience**: `YoloClient` retries connection failures, timeouts, and
  5xx responses from `citymind-yolo-service` with bounded exponential
  backoff (via `tenacity`), and never retries 4xx client errors. All calls,
  retries, and failures are logged.
- **No business logic in the HTTP client**: `yolo_client.py` only knows how
  to make requests and parse JSON. All incident logic lives in
  `event_aggregator.py`.
- **Separation from citymind-yolo-service**: this project never imports
  from or modifies that service. `app/models/detection.py` is an
  intentionally independent mirror of its wire format, used purely for
  request/response typing on this side of the HTTP boundary.
