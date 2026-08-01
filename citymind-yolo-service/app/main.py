"""
citymind-yolo-service — FastAPI entrypoint.

A standalone computer-vision microservice. Given an image, a video file, a
webcam device index, or a live stream URL (RTSP/HTTP), it runs vehicle
detection + tracking, accident detection, and emergency-vehicle detection,
and returns standardized JSON `DetectionEvent`s.

This service knows nothing about traffic simulation, infrastructure
planning, dashboards, or the AI copilot layers above it in CityMind's
architecture — it only reports observations.
"""

from __future__ import annotations

import logging
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.config import settings
from app.detector import AccidentDetector, AccidentMotionEvaluator, EmergencyVehicleDetector
from app.event_generator import build_event
from app.schemas import (
    DetectionEvent,
    HealthResponse,
    StreamDetectionRequest,
    VideoDetectionResponse,
    WebcamDetectionRequest,
)
from app.tracker import VehicleTracker
from app.utils import decode_image_bytes, iter_frames, open_video_source, open_webcam

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)

# Models are heavy to load — one instance each, created once at startup and
# shared across requests. Per-request state (motion history for accident
# detection, tracker identity state) is scoped per request, not shared.
vehicle_tracker: VehicleTracker | None = None
accident_detector: AccidentDetector | None = None
emergency_detector: EmergencyVehicleDetector | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global vehicle_tracker, accident_detector, emergency_detector
    logger.info("loading models...")
    vehicle_tracker = VehicleTracker()
    accident_detector = AccidentDetector()
    emergency_detector = EmergencyVehicleDetector()
    logger.info(
        "models ready (vehicle=%s, accident=%s)",
        vehicle_tracker.is_loaded,
        accident_detector.is_loaded,
    )
    yield


app = FastAPI(
    title=settings.service_name,
    description="Pure computer-vision layer for CityMind: vehicle detection/tracking, "
    "accident detection, emergency-vehicle detection, and traffic-density estimation.",
    version="1.0.0",
    lifespan=lifespan,
)


def _process_source(
    open_fn,
    camera_id: str,
    max_frames: int,
    static_image: bool = False,
) -> list[DetectionEvent]:
    """
    Shared loop for video/webcam/stream endpoints: reset tracker identity
    state, open the source, run every kept frame through the pipeline, and
    collect one DetectionEvent per processed frame.
    """
    vehicle_tracker.reset_tracking_state()
    motion_evaluator = AccidentMotionEvaluator(detector=accident_detector)

    cap = open_fn()
    if cap is None or not cap.isOpened():
        raise HTTPException(status_code=400, detail="failed to open source")

    events: list[DetectionEvent] = []
    for frame_idx, frame in iter_frames(cap, frame_skip=settings.frame_skip, max_frames=max_frames):
        vehicles = vehicle_tracker.track(frame, persist=True)
        accident_detections = motion_evaluator.evaluate(frame, vehicles, static_image=static_image)
        is_emergency = emergency_detector.detect(frame, vehicles)

        events.append(
            build_event(
                camera_id=camera_id,
                vehicles=vehicles,
                accident_detections=accident_detections,
                emergency_detected=is_emergency,
                frame_index=frame_idx,
            )
        )

    return events


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        service=settings.service_name,
        yolo_model_loaded=vehicle_tracker.is_loaded if vehicle_tracker else False,
        accident_model_loaded=accident_detector.is_loaded if accident_detector else False,
    )


@app.post("/detect/image", response_model=DetectionEvent)
async def detect_image(file: UploadFile = File(...), camera_id: str = settings.default_camera_id) -> DetectionEvent:
    data = await file.read()
    frame = decode_image_bytes(data)
    if frame is None:
        raise HTTPException(status_code=400, detail="could not decode uploaded image")

    vehicles = vehicle_tracker.detect(frame)
    accident_detections = accident_detector.detect(frame)  # static image: trust the raw model output
    is_emergency = emergency_detector.detect(frame, vehicles)

    return build_event(
        camera_id=camera_id,
        vehicles=vehicles,
        accident_detections=accident_detections,
        emergency_detected=is_emergency,
    )


@app.post("/detect/video", response_model=VideoDetectionResponse)
async def detect_video(
    file: UploadFile = File(...),
    camera_id: str = settings.default_camera_id,
    max_frames: int = settings.max_frames_per_request,
) -> VideoDetectionResponse:
    max_frames = min(max_frames, settings.max_frames_per_request)
    suffix = Path(file.filename or "upload.mp4").suffix or ".mp4"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
        tmp.write(await file.read())
        tmp.flush()

        events = _process_source(
            open_fn=lambda: open_video_source(tmp.name),
            camera_id=camera_id,
            max_frames=max_frames,
        )

    return VideoDetectionResponse(
        camera_id=camera_id,
        road_id=events[0].road_id if events else None,
        frames_processed=len(events),
        events=events,
    )


@app.post("/detect/webcam", response_model=VideoDetectionResponse)
async def detect_webcam(request: WebcamDetectionRequest) -> VideoDetectionResponse:
    max_frames = min(request.max_frames, settings.max_frames_per_request)

    events = _process_source(
        open_fn=lambda: open_webcam(request.device_index),
        camera_id=request.camera_id,
        max_frames=max_frames,
    )

    return VideoDetectionResponse(
        camera_id=request.camera_id,
        road_id=events[0].road_id if events else None,
        frames_processed=len(events),
        events=events,
    )


@app.post("/detect/stream", response_model=VideoDetectionResponse)
async def detect_stream(request: StreamDetectionRequest) -> VideoDetectionResponse:
    max_frames = min(request.max_frames or settings.max_frames_per_request, settings.max_frames_per_request)

    events = _process_source(
        open_fn=lambda: open_video_source(request.source_url),
        camera_id=request.camera_id,
        max_frames=max_frames,
    )

    return VideoDetectionResponse(
        camera_id=request.camera_id,
        road_id=events[0].road_id if events else None,
        frames_processed=len(events),
        events=events,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "internal error"})
