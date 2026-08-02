"""
HTTP routes for citymind-backend.

Every endpoint here follows the same shape: receive input, hand it to the
`YoloClient` to get frame-level detections from citymind-yolo-service, feed
those detections through the `EventAggregator` to get incident-level
results, and return an `AggregationResult`. No detection or aggregation
logic lives in this module — it is purely the glue between HTTP and the
service layer.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.detection import RawDetectionEvent, RawVideoDetectionResponse, YoloHealthResponse
from app.models.incident import AggregationResult, Incident
from app.repositories.detection_repository import DetectionEventRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.event_aggregator import EventAggregator
from app.services.realtime import RealtimeHub
from app.services.yolo_client import YoloClient, YoloClientError, YoloConnectionError, YoloResponseError
from app.utils.cache import dashboard_cache

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------
# Request/response schemas local to the API layer
# --------------------------------------------------------------------------


class StreamAggregationRequest(BaseModel):
    """Body for POST /aggregate/stream."""

    source_url: str = Field(..., description="Any URL the YOLO service can open (e.g. an RTSP or HTTP stream).")
    camera_id: str = Field(default=settings.default_camera_id)
    max_frames: int | None = Field(default=None, ge=1)


class HealthResponse(BaseModel):
    status: str
    service: str
    yolo_service_reachable: bool
    yolo_service: YoloHealthResponse | None = None
    yolo_service_error: str | None = None


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------


def get_yolo_client(request: Request) -> YoloClient:
    return request.app.state.yolo_client


def get_event_aggregator(request: Request) -> EventAggregator:
    return request.app.state.event_aggregator


def get_realtime_hub(request: Request) -> RealtimeHub:
    return request.app.state.realtime_hub


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


async def _aggregate_events(
    aggregator: EventAggregator,
    events: list[RawDetectionEvent],
    camera_id: str,
    finalize: bool,
    db: AsyncSession | None = None,
    realtime_hub: RealtimeHub | None = None,
) -> tuple[list[Incident], list[Incident]]:
    """Feed events through the aggregator and return (closed_incidents, active_incidents).

    When `finalize` is True, any incident still active for `camera_id` after
    processing all events is force-closed. This is correct for bounded units
    of work (a single uploaded video, a single stream-polling request) where
    there is no guarantee a future request will ever arrive to naturally
    close the incident via a cooldown gap.

    When `db` is provided, every raw event is persisted as a `DetectionEvent`
    row and every closed incident is upserted into `incidents` — best-effort,
    logged but never fatal to the request (a persistence failure shouldn't
    break a live detection pipeline).
    """
    closed = await aggregator.ingest_many(events)

    if finalize:
        forced = await aggregator.close_active_incident(camera_id)
        if forced is not None:
            closed.append(forced)

    active = await aggregator.get_active_incidents(camera_id=camera_id)

    if db is not None:
        try:
            incident_id = closed[-1].incident_id if closed else None
            await DetectionEventRepository(db).create_many_from_schema(events, incident_id=incident_id)
            incident_repo = IncidentRepository(db)
            for incident in closed:
                await incident_repo.upsert_from_schema(incident)
            if closed:
                await dashboard_cache.invalidate()
        except Exception:  # noqa: BLE001 - persistence must not break live detection responses
            logger.exception("failed to persist detection events/incidents for camera %s", camera_id)

    if realtime_hub is not None:
        try:
            for incident in closed:
                await realtime_hub.broadcast("incident.closed", incident.model_dump())
            if active:
                await realtime_hub.broadcast(
                    "camera.active_incidents",
                    {"camera_id": camera_id, "incidents": [i.model_dump() for i in active]},
                )
        except Exception:  # noqa: BLE001 - realtime push must never break the HTTP response
            logger.exception("failed to broadcast realtime event for camera %s", camera_id)

    return closed, active


def _raise_for_yolo_error(exc: YoloClientError) -> None:
    if isinstance(exc, YoloConnectionError):
        logger.error("YOLO service unreachable: %s", exc)
        raise HTTPException(status_code=502, detail=f"citymind-yolo-service unreachable: {exc}") from exc
    if isinstance(exc, YoloResponseError):
        logger.error("YOLO service returned an error: %s", exc)
        status_code = exc.status_code if exc.status_code and exc.status_code < 500 else 502
        raise HTTPException(
            status_code=status_code, detail={"message": str(exc), "upstream": exc.detail}
        ) from exc
    logger.error("unexpected YOLO client error: %s", exc)
    raise HTTPException(status_code=502, detail=f"citymind-yolo-service error: {exc}") from exc


async def _read_upload(file: UploadFile, expected_prefix: str) -> bytes:
    """Basic sanity checks before forwarding a file to ml-service.

    Deliberately lightweight — this is not a security boundary, just a way
    to fail fast with a clear message instead of forwarding an obviously
    wrong file and getting a confusing error back from ml-service.
    """
    content_type = file.content_type or ""
    if content_type and not content_type.startswith(expected_prefix):
        kind = expected_prefix.rstrip("/")
        article = "an" if kind[0] in "aeiou" else "a"
        raise HTTPException(
            status_code=400,
            detail=f"expected {article} {kind} file, got content-type '{content_type}'",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="uploaded file is empty")
    return data


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@router.get("/health", response_model=HealthResponse)
async def health(yolo_client: YoloClient = Depends(get_yolo_client)) -> HealthResponse:
    """Backend liveness plus best-effort reachability check of citymind-yolo-service."""
    try:
        raw = await yolo_client.health()
        return HealthResponse(
            status="ok",
            service=settings.service_name,
            yolo_service_reachable=True,
            yolo_service=YoloHealthResponse(**raw),
        )
    except YoloClientError as exc:
        logger.warning("YOLO service health check failed: %s", exc)
        return HealthResponse(
            status="degraded",
            service=settings.service_name,
            yolo_service_reachable=False,
            yolo_service_error=str(exc),
        )




@router.post("/detect")
async def detect(
    file: UploadFile,
    camera_id: str | None = None,
    yolo_client: YoloClient = Depends(get_yolo_client),
):
    """Direct end-to-end detection endpoint returning raw ML detections/events."""
    data = await _read_upload(file, expected_prefix="image/")
    try:
        return await yolo_client.detect_image(
            file_bytes=data,
            filename=file.filename or "upload.jpg",
            content_type=file.content_type or "application/octet-stream",
            camera_id=camera_id,
        )
    except YoloClientError as exc:
        _raise_for_yolo_error(exc)

@router.post("/aggregate/image", response_model=AggregationResult)
async def aggregate_image(
    file: UploadFile,
    camera_id: str | None = None,
    yolo_client: YoloClient = Depends(get_yolo_client),
    aggregator: EventAggregator = Depends(get_event_aggregator),
    db: AsyncSession = Depends(get_db),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> AggregationResult:
    """Upload a single image, run it through YOLO detection, and update incident state."""
    data = await _read_upload(file, expected_prefix="image/")

    try:
        raw = await yolo_client.detect_image(
            file_bytes=data,
            filename=file.filename or "upload.jpg",
            content_type=file.content_type or "application/octet-stream",
            camera_id=camera_id,
        )
    except YoloClientError as exc:
        _raise_for_yolo_error(exc)

    event = RawDetectionEvent(**raw)
    closed, active = await _aggregate_events(
        aggregator=aggregator,
        events=[event],
        camera_id=event.camera_id,
        finalize=False,
        db=db,
        realtime_hub=realtime_hub,
    )

    return AggregationResult(
        camera_id=event.camera_id,
        road_id=event.road_id,
        frames_processed=1,
        incidents=closed,
        active_incidents=active,
        incident_count=len(closed),
        active_incident_count=len(active),
    )


@router.post("/aggregate/video", response_model=AggregationResult)
async def aggregate_video(
    file: UploadFile,
    camera_id: str | None = None,
    max_frames: int | None = None,
    yolo_client: YoloClient = Depends(get_yolo_client),
    aggregator: EventAggregator = Depends(get_event_aggregator),
    db: AsyncSession = Depends(get_db),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> AggregationResult:
    """Upload a video file, run it through YOLO detection, and aggregate every frame into incidents."""
    data = await _read_upload(file, expected_prefix="video/")

    try:
        raw = await yolo_client.detect_video(
            file_bytes=data,
            filename=file.filename or "upload.mp4",
            content_type=file.content_type or "application/octet-stream",
            camera_id=camera_id,
            max_frames=max_frames,
        )
    except YoloClientError as exc:
        _raise_for_yolo_error(exc)

    parsed = RawVideoDetectionResponse(**raw)
    resolved_camera_id = camera_id or parsed.camera_id

    closed, active = await _aggregate_events(
        aggregator=aggregator,
        events=parsed.events,
        camera_id=resolved_camera_id,
        finalize=True,
        db=db,
        realtime_hub=realtime_hub,
    )

    return AggregationResult(
        camera_id=resolved_camera_id,
        road_id=parsed.road_id,
        frames_processed=parsed.frames_processed,
        incidents=closed,
        active_incidents=active,
        incident_count=len(closed),
        active_incident_count=len(active),
    )


@router.post("/aggregate/stream", response_model=AggregationResult)
async def aggregate_stream(
    payload: StreamAggregationRequest,
    yolo_client: YoloClient = Depends(get_yolo_client),
    aggregator: EventAggregator = Depends(get_event_aggregator),
    db: AsyncSession = Depends(get_db),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> AggregationResult:
    """Connect to a live stream (RTSP/HTTP) via the YOLO service and aggregate the frames it returns."""
    try:
        raw = await yolo_client.detect_stream(
            source_url=payload.source_url,
            camera_id=payload.camera_id,
            max_frames=payload.max_frames,
        )
    except YoloClientError as exc:
        _raise_for_yolo_error(exc)

    parsed = RawVideoDetectionResponse(**raw)
    resolved_camera_id = payload.camera_id or parsed.camera_id

    closed, active = await _aggregate_events(
        aggregator=aggregator,
        events=parsed.events,
        camera_id=resolved_camera_id,
        finalize=True,
        db=db,
        realtime_hub=realtime_hub,
    )

    return AggregationResult(
        camera_id=resolved_camera_id,
        road_id=parsed.road_id,
        frames_processed=parsed.frames_processed,
        incidents=closed,
        active_incidents=active,
        incident_count=len(closed),
        active_incident_count=len(active),
    )
