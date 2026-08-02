"""
ORM models (SQLAlchemy 2.x) persisted to PostgreSQL.

These are the *storage* representations of CityMind's data and are
intentionally separate from the Pydantic wire models in `app.models` /
`app.schemas` (which describe HTTP request/response bodies). Repositories
in `app.repositories` translate between the two.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _uuid_str() -> str:
    return str(uuid.uuid4())


class Incident(Base):
    """A deduplicated, incident-level accident event (mirrors `app.models.incident.Incident`)."""

    __tablename__ = "incidents"

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid_str)
    incident_type: Mapped[str] = mapped_column(String(32), nullable=False, default="ACCIDENT")
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)

    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    road_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    last_seen_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    frame_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    emergency_vehicle_present: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    peak_vehicle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    traffic_density_at_peak: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW")

    first_frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    detection_events: Mapped[list["DetectionEvent"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_incidents_camera_status", "camera_id", "status"),
        Index("ix_incidents_start_time_desc", "start_time"),
    )


class Facility(Base):
    """A recommended/placed facility location (output of `/city/recommend-facility`)."""

    __tablename__ = "facilities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    facility_type: Mapped[str] = mapped_column(String(64), nullable=False, default="GENERIC", index=True)

    node_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)

    cost: Mapped[float] = mapped_column(Float, nullable=False)
    n_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    candidate_costs: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Facility-catalog spacing metadata: the minimum spacing constraint that
    # was applied when this site was chosen, and how many existing same-type
    # facilities were considered against it.
    min_spacing_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    existing_facilities_considered: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="RECOMMENDED", index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class DetectionEvent(Base):
    """A single frame-level detection observation (mirrors `app.models.detection.RawDetectionEvent`)."""

    __tablename__ = "detection_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    camera_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    road_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    frame_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    accident: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    vehicle_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vehicle_cars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vehicle_buses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vehicle_trucks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vehicle_motorcycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vehicle_bicycles: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    traffic_density: Mapped[str] = mapped_column(String(16), nullable=False, default="LOW", index=True)
    emergency_vehicle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    incident_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("incidents.incident_id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    incident: Mapped["Incident | None"] = relationship(back_populates="detection_events")

    __table_args__ = (Index("ix_detection_events_camera_ts", "camera_id", "timestamp"),)


class RouteAnalysis(Base):
    """A persisted result of `/city/route` or `/city/simulate-closure`."""

    __tablename__ = "route_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    analysis_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)  # "ROUTE" | "CLOSURE"
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OK", index=True)

    origin_lat: Mapped[float] = mapped_column(Float, nullable=False)
    origin_lon: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lat: Mapped[float] = mapped_column(Float, nullable=False)
    dest_lon: Mapped[float] = mapped_column(Float, nullable=False)

    distance_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    route_node_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    route_coordinates: Mapped[list | None] = mapped_column(JSON, nullable=True)

    closed_edge_u: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    closed_edge_v: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    length_before_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    length_after_km: Mapped[float | None] = mapped_column(Float, nullable=True)
    delay_km: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    __table_args__ = (Index("ix_route_analyses_type_created", "analysis_type", "created_at"),)
