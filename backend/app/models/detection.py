"""
Wire-format models for what citymind-yolo-service returns.

citymind-backend is a separate project and must never import
citymind-yolo-service's code, so these models are an independent,
intentionally minimal mirror of that service's `DetectionEvent` /
`VideoDetectionResponse` schemas. They exist purely so the aggregator can
work with typed, validated objects instead of raw dicts.
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


class RawDetectionEvent(BaseModel):
    """One frame-level observation, exactly as produced by the YOLO service."""

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


class RawVideoDetectionResponse(BaseModel):
    """Multi-frame response, exactly as produced by /detect/video, /detect/stream, /detect/webcam."""

    camera_id: str
    road_id: str | None = None
    frames_processed: int
    events: list[RawDetectionEvent] = Field(default_factory=list)


class YoloHealthResponse(BaseModel):
    status: str = "ok"
    service: str | None = None
    yolo_model_loaded: bool = False
    accident_model_loaded: bool = False
