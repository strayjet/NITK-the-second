# citymind-yolo-service

Standalone computer-vision microservice — **Layer 1** of the CityMind architecture:

```
YOLO Detection Service   <-- this repo
        ↓
FastAPI Simulation Engine
        ↓
Traffic Simulation / Infrastructure Planning
        ↓
AI Copilot
        ↓
React Dashboard
```

This service does exactly one job: turn a camera frame (image, video, webcam,
or live stream) into a standardized JSON observation. It has no opinion about
what should happen next — no simulation, no planning, no dashboards, no auth,
no database. That all belongs to layers above this one.

## Yolo service structure

```
citymind-yolo-service/
    app/
        main.py            FastAPI app + the 5 endpoints
        detector.py         Accident detection + emergency-vehicle detection
        tracker.py           Vehicle detection + ByteTrack tracking
        event_generator.py    Assembles the standardized JSON event
        config.py             All tunables, env-driven
        schemas.py             Pydantic request/response models
        camera_mapper.py        camera_id -> road/lat/lng lookup
        utils.py                  Frame decode + video source iteration
    weights/
        yolov8n.pt            Vehicle detection base model
        accident_best.pt       Dedicated accident-detection model
    config/
        camera_map.json         Example camera metadata
    requirements.txt
    .env.example
```

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional, defaults work out of the box
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## Endpoints

All endpoints return JSON only — no HTML, no templates.

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/health` | — | Service + model load status |
| POST | `/detect/image` | multipart file + `camera_id` query param | Runs one static frame through the pipeline |
| POST | `/detect/video` | multipart video file + `camera_id`, `max_frames` | Processes an uploaded video, frame-skipped per `FRAME_SKIP` |
| POST | `/detect/webcam` | JSON `{device_index, camera_id, max_frames}` | Reads N frames from a local camera device |
| POST | `/detect/stream` | JSON `{source_url, camera_id, max_frames}` | Reads from any URL OpenCV can open, including `rtsp://` |

### Example event

```json
{
    "camera_id": "CAM_14",
    "road_id": "MG_ROAD",
    "lat": 12.9721,
    "lng": 77.5951,
    "timestamp": "2026-08-01T12:00:00+00:00",
    "frame_index": 42,
    "accident": true,
    "confidence": 0.97,
    "vehicles": {
        "total": 14,
        "cars": 11,
        "buses": 1,
        "trucks": 1,
        "motorcycles": 1,
        "bicycles": 0
    },
    "traffic_density": "HIGH",
    "emergency_vehicle": false,
    "bounding_boxes": [
        {"label": "car", "confidence": 0.91, "track_id": 7, "x1": 120.0, "y1": 84.0, "x2": 210.0, "y2": 160.0}
    ]
}
```

`/detect/video`, `/detect/webcam`, and `/detect/stream` return this same
`DetectionEvent` shape once per processed frame, wrapped in:

```json
{
    "camera_id": "CAM_14",
    "road_id": "MG_ROAD",
    "frames_processed": 40,
    "events": [ /* DetectionEvent, ... */ ]
}
```

## Camera mapping

`config/camera_map.json` maps a `camera_id` to its road name and
coordinates. Every event is enriched with this metadata automatically — the
detector itself only ever sees a `camera_id` string, never location data
directly. Add new cameras by editing this file; no code changes needed.

## Configuration

Every tunable (confidence thresholds, model paths, device, frame-skip rate,
density thresholds, frame caps) lives in `app/config.py` and is overridable
via environment variables — see `.env.example` for the full list.

## Notes on the tracking-identity reset

Vehicle and accident models are loaded once at startup and shared across
requests for performance. Because ByteTrack keeps identity state on the
model instance, `VehicleTracker.reset_tracking_state()` is called at the
start of every `/detect/video`, `/detect/webcam`, and `/detect/stream`
request so track IDs don't leak between unrelated sources.
