"""initial schema: incidents, facilities, detection_events, route_analyses

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-01 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(length=64), primary_key=True),
        sa.Column("incident_type", sa.String(length=32), nullable=False, server_default="ACCIDENT"),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("camera_id", sa.String(length=128), nullable=False),
        sa.Column("road_id", sa.String(length=128), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=False, server_default="0"),
        sa.Column("frame_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("avg_confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("emergency_vehicle_present", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("peak_vehicle_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("traffic_density_at_peak", sa.String(length=16), nullable=False, server_default="LOW"),
        sa.Column("first_frame_index", sa.Integer(), nullable=True),
        sa.Column("last_frame_index", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_incidents_status", "incidents", ["status"])
    op.create_index("ix_incidents_camera_id", "incidents", ["camera_id"])
    op.create_index("ix_incidents_road_id", "incidents", ["road_id"])
    op.create_index("ix_incidents_start_time", "incidents", ["start_time"])
    op.create_index("ix_incidents_camera_status", "incidents", ["camera_id", "status"])
    op.create_index("ix_incidents_start_time_desc", "incidents", ["start_time"])

    op.create_table(
        "facilities",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("facility_type", sa.String(length=64), nullable=False, server_default="GENERIC"),
        sa.Column("node_id", sa.String(length=64), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("n_candidates", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_costs", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="RECOMMENDED"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_facilities_facility_type", "facilities", ["facility_type"])
    op.create_index("ix_facilities_node_id", "facilities", ["node_id"])
    op.create_index("ix_facilities_status", "facilities", ["status"])
    op.create_index("ix_facilities_created_at", "facilities", ["created_at"])

    op.create_table(
        "detection_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("camera_id", sa.String(length=128), nullable=False),
        sa.Column("road_id", sa.String(length=128), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frame_index", sa.Integer(), nullable=True),
        sa.Column("accident", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("vehicle_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vehicle_cars", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vehicle_buses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vehicle_trucks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vehicle_motorcycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("vehicle_bicycles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("traffic_density", sa.String(length=16), nullable=False, server_default="LOW"),
        sa.Column("emergency_vehicle", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "incident_id",
            sa.String(length=64),
            sa.ForeignKey("incidents.incident_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_detection_events_camera_id", "detection_events", ["camera_id"])
    op.create_index("ix_detection_events_road_id", "detection_events", ["road_id"])
    op.create_index("ix_detection_events_timestamp", "detection_events", ["timestamp"])
    op.create_index("ix_detection_events_accident", "detection_events", ["accident"])
    op.create_index("ix_detection_events_traffic_density", "detection_events", ["traffic_density"])
    op.create_index("ix_detection_events_incident_id", "detection_events", ["incident_id"])
    op.create_index("ix_detection_events_camera_ts", "detection_events", ["camera_id", "timestamp"])

    op.create_table(
        "route_analyses",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("analysis_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="OK"),
        sa.Column("origin_lat", sa.Float(), nullable=False),
        sa.Column("origin_lon", sa.Float(), nullable=False),
        sa.Column("dest_lat", sa.Float(), nullable=False),
        sa.Column("dest_lon", sa.Float(), nullable=False),
        sa.Column("distance_km", sa.Float(), nullable=True),
        sa.Column("route_node_count", sa.Integer(), nullable=True),
        sa.Column("route_coordinates", sa.JSON(), nullable=True),
        sa.Column("closed_edge_u", sa.BigInteger(), nullable=True),
        sa.Column("closed_edge_v", sa.BigInteger(), nullable=True),
        sa.Column("length_before_km", sa.Float(), nullable=True),
        sa.Column("length_after_km", sa.Float(), nullable=True),
        sa.Column("delay_km", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_route_analyses_analysis_type", "route_analyses", ["analysis_type"])
    op.create_index("ix_route_analyses_status", "route_analyses", ["status"])
    op.create_index("ix_route_analyses_created_at", "route_analyses", ["created_at"])
    op.create_index("ix_route_analyses_type_created", "route_analyses", ["analysis_type", "created_at"])


def downgrade() -> None:
    op.drop_table("route_analyses")
    op.drop_table("detection_events")
    op.drop_table("facilities")
    op.drop_table("incidents")
