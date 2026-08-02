"""
Incident-level models produced by the Event Aggregator.

Where `RawDetectionEvent` (see `app.models.detection`) is a single
frame-level observation coming out of the YOLO service, an `Incident` is the
aggregator's synthesis of many such frames into one continuous real-world
event — e.g. "an accident was visible on camera CAM_04 from 10:03:12 to
10:03:41, peaking at confidence 0.94, with an emergency vehicle present".
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.detection import TrafficDensity

IncidentStatus = Literal["ACTIVE", "CLOSED"]
IncidentType = Literal["ACCIDENT"]


class Incident(BaseModel):
    """A single, deduplicated, incident-level event."""

    incident_id: str
    incident_type: IncidentType = "ACCIDENT"
    status: IncidentStatus

    camera_id: str
    road_id: str | None = None
    lat: float | None = None
    lng: float | None = None

    start_time: datetime
    last_seen_time: datetime
    end_time: datetime | None = None
    duration_seconds: float = 0.0

    frame_count: int = 0
    max_confidence: float = 0.0
    avg_confidence: float = 0.0

    emergency_vehicle_present: bool = False
    peak_vehicle_count: int = 0
    traffic_density_at_peak: TrafficDensity = "LOW"

    first_frame_index: int | None = None
    last_frame_index: int | None = None


class AggregationResult(BaseModel):
    """Response payload returned by the /aggregate/* endpoints."""

    camera_id: str
    road_id: str | None = None
    frames_processed: int = 0

    # Incidents that were closed as a direct result of processing this request.
    incidents: list[Incident] = Field(default_factory=list)

    # Snapshot of incidents still open for this camera after processing this
    # request (useful for a continuously-polled/live camera feed).
    active_incidents: list[Incident] = Field(default_factory=list)

    incident_count: int = 0
    active_incident_count: int = 0
