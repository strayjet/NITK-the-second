"""Response schemas for `/dashboard/*` endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class DashboardSummaryResponse(BaseModel):
    total_incidents: int
    active_incidents: int
    closed_incidents: int
    total_facilities: int
    total_detection_events: int
    total_route_analyses: int
    avg_route_distance_km: float
    incidents_by_status: dict[str, int]
    facilities_by_type: dict[str, int]
    generated_at: datetime
    cached: bool = False


class IncidentSummary(BaseModel):
    incident_id: str
    incident_type: str
    status: str
    camera_id: str
    road_id: str | None = None
    lat: float | None = None
    lng: float | None = None
    start_time: datetime
    last_seen_time: datetime
    end_time: datetime | None = None
    duration_seconds: float
    frame_count: int
    max_confidence: float
    avg_confidence: float
    emergency_vehicle_present: bool
    peak_vehicle_count: int
    traffic_density_at_peak: str

    model_config = {"from_attributes": True}


class FacilitySummary(BaseModel):
    id: int
    name: str | None = None
    facility_type: str
    node_id: str
    lat: float
    lon: float
    cost: float
    n_candidates: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class CongestionSegment(BaseModel):
    road_id: str
    sample_count: int
    avg_vehicle_count: float
    avg_density_score: float
    accident_count: int


class CongestionResponse(BaseModel):
    segments: list[CongestionSegment]
    generated_at: datetime
    cached: bool = False


class RouteAnalysisSummary(BaseModel):
    id: int
    analysis_type: str
    status: str
    origin_lat: float
    origin_lon: float
    dest_lat: float
    dest_lon: float
    distance_km: float | None = None
    route_node_count: int | None = None
    length_before_km: float | None = None
    length_after_km: float | None = None
    delay_km: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
