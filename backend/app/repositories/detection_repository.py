"""Repository for the `detection_events` table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DetectionEvent
from app.models.detection import RawDetectionEvent
from app.utils.time_utils import parse_timestamp


class DetectionEventRepository:
    """Async CRUD + query helpers for `DetectionEvent` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_from_schema(
        self, event: RawDetectionEvent, *, incident_id: str | None = None
    ) -> DetectionEvent:
        row = DetectionEvent(
            camera_id=event.camera_id,
            road_id=event.road_id,
            lat=event.lat,
            lng=event.lng,
            timestamp=parse_timestamp(event.timestamp),
            frame_index=event.frame_index,
            accident=event.accident,
            confidence=event.confidence,
            vehicle_total=event.vehicles.total,
            vehicle_cars=event.vehicles.cars,
            vehicle_buses=event.vehicles.buses,
            vehicle_trucks=event.vehicles.trucks,
            vehicle_motorcycles=event.vehicles.motorcycles,
            vehicle_bicycles=event.vehicles.bicycles,
            traffic_density=event.traffic_density,
            emergency_vehicle=event.emergency_vehicle,
            incident_id=incident_id,
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_many_from_schema(
        self, events: list[RawDetectionEvent], *, incident_id: str | None = None
    ) -> list[DetectionEvent]:
        rows = [
            DetectionEvent(
                camera_id=e.camera_id,
                road_id=e.road_id,
                lat=e.lat,
                lng=e.lng,
                timestamp=parse_timestamp(e.timestamp),
                frame_index=e.frame_index,
                accident=e.accident,
                confidence=e.confidence,
                vehicle_total=e.vehicles.total,
                vehicle_cars=e.vehicles.cars,
                vehicle_buses=e.vehicles.buses,
                vehicle_trucks=e.vehicles.trucks,
                vehicle_motorcycles=e.vehicles.motorcycles,
                vehicle_bicycles=e.vehicles.bicycles,
                traffic_density=e.traffic_density,
                emergency_vehicle=e.emergency_vehicle,
                incident_id=incident_id,
            )
            for e in events
        ]
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def list_filtered(
        self,
        *,
        camera_id: str | None = None,
        road_id: str | None = None,
        traffic_density: str | None = None,
        accident_only: bool = False,
        since: datetime | None = None,
        until: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[DetectionEvent], int]:
        conditions = self._build_conditions(
            camera_id=camera_id,
            road_id=road_id,
            traffic_density=traffic_density,
            accident_only=accident_only,
            since=since,
            until=until,
        )

        count_stmt = select(func.count()).select_from(DetectionEvent)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = select(DetectionEvent).order_by(DetectionEvent.timestamp.desc()).offset(offset).limit(limit)
        for cond in conditions:
            stmt = stmt.where(cond)
        rows = (await self.session.execute(stmt)).scalars().all()

        return list(rows), total

    async def congestion_by_road(self, *, since: datetime | None = None, limit: int = 50) -> list[dict]:
        """Aggregate average traffic density / vehicle volume per road, most congested first."""
        density_rank = func.avg(
            func.case(
                (DetectionEvent.traffic_density == "HIGH", 2),
                (DetectionEvent.traffic_density == "MEDIUM", 1),
                else_=0,
            )
        )
        accident_count = func.sum(func.cast(DetectionEvent.accident, Integer))

        stmt = (
            select(
                DetectionEvent.road_id,
                func.count().label("sample_count"),
                func.avg(DetectionEvent.vehicle_total).label("avg_vehicle_count"),
                density_rank.label("avg_density_score"),
                accident_count.label("accident_count"),
            )
            .where(DetectionEvent.road_id.is_not(None))
            .group_by(DetectionEvent.road_id)
            .order_by(density_rank.desc())
            .limit(limit)
        )
        if since:
            stmt = stmt.where(DetectionEvent.timestamp >= since)

        rows = (await self.session.execute(stmt)).all()
        return [
            {
                "road_id": road_id,
                "sample_count": sample_count,
                "avg_vehicle_count": round(float(avg_vehicle_count or 0), 2),
                "avg_density_score": round(float(avg_density_score or 0), 2),
                "accident_count": int(accident_count or 0),
            }
            for road_id, sample_count, avg_vehicle_count, avg_density_score, accident_count in rows
        ]

    async def count_total(self) -> int:
        stmt = select(func.count()).select_from(DetectionEvent)
        return (await self.session.execute(stmt)).scalar_one()

    @staticmethod
    def _build_conditions(
        *,
        camera_id: str | None,
        road_id: str | None,
        traffic_density: str | None,
        accident_only: bool,
        since: datetime | None,
        until: datetime | None,
    ) -> list:
        conditions = []
        if camera_id:
            conditions.append(DetectionEvent.camera_id == camera_id)
        if road_id:
            conditions.append(DetectionEvent.road_id == road_id)
        if traffic_density:
            conditions.append(DetectionEvent.traffic_density == traffic_density)
        if accident_only:
            conditions.append(DetectionEvent.accident.is_(True))
        if since:
            conditions.append(DetectionEvent.timestamp >= since)
        if until:
            conditions.append(DetectionEvent.timestamp <= until)
        return conditions
