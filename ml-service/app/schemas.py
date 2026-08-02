"""
Pydantic models for citymind-yolo-service.

These define the exact wire format the service speaks. `DetectionEvent` is
the canonical unit of output — every endpoint returns one or many of these.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TrafficDensity = Literal["LOW", "MEDIUM", "HIGH"]


class BoundingBox(BaseModel):
    label: str
    confidence: float
    track_id: int | None = None
    x1: float
    y1: float
    x2: float
    y2: float


class VehicleCounts(BaseModel):
    total: int = 0
    cars: int = 0
    buses: int = 0
    trucks: int = 0
    motorcycles: int = 0
    bicycles: int = 0


class DetectionEvent(BaseModel):
    """A single standardized observation produced from one processed frame."""

    camera_id: str
    road_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    timestamp: str
    frame_index: int | None = None

    accident: bool = False
    confidence: float = 0.0

    vehicles: VehicleCounts = Field(default_factory=VehicleCounts)
    traffic_density: TrafficDensity = "LOW"
    emergency_vehicle: bool = False

    bounding_boxes: list[BoundingBox] = Field(default_factory=list)


class VideoDetectionResponse(BaseModel):
    camera_id: str
    road_id: str | None = None
    frames_processed: int
    events: list[DetectionEvent]


class StreamDetectionRequest(BaseModel):
    """Body for POST /detect/stream — any URL cv2.VideoCapture can open, incl. RTSP."""

    source_url: str
    camera_id: str = "UNKNOWN_CAM"
    max_frames: int | None = None


class WebcamDetectionRequest(BaseModel):
    """Body for POST /detect/webcam — reads from a local camera device index."""

    device_index: int = 0
    camera_id: str = "UNKNOWN_CAM"
    max_frames: int = 30


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    yolo_model_loaded: bool
    accident_model_loaded: bool
