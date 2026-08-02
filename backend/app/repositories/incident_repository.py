"""Repository for the `incidents` table."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Incident
from app.models.incident import Incident as IncidentSchema


class IncidentRepository:
    """Async CRUD + query helpers for `Incident` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert_from_schema(self, incident: IncidentSchema) -> Incident:
        """Insert or update a DB row from an aggregator-produced `Incident` pydantic model."""
        existing = await self.session.get(Incident, incident.incident_id)
        if existing is None:
            existing = Incident(incident_id=incident.incident_id)
            self.session.add(existing)

        existing.incident_type = incident.incident_type
        existing.status = incident.status
        existing.camera_id = incident.camera_id
        existing.road_id = incident.road_id
        existing.lat = incident.lat
        existing.lng = incident.lng
        existing.start_time = incident.start_time
        existing.last_seen_time = incident.last_seen_time
        existing.end_time = incident.end_time
        existing.duration_seconds = incident.duration_seconds
        existing.frame_count = incident.frame_count
        existing.max_confidence = incident.max_confidence
        existing.avg_confidence = incident.avg_confidence
        existing.emergency_vehicle_present = incident.emergency_vehicle_present
        existing.peak_vehicle_count = incident.peak_vehicle_count
        existing.traffic_density_at_peak = incident.traffic_density_at_peak
        existing.first_frame_index = incident.first_frame_index
        existing.last_frame_index = incident.last_frame_index

        await self.session.flush()
        return existing

    async def get(self, incident_id: str) -> Incident | None:
        return await self.session.get(Incident, incident_id)

    async def list_filtered(
        self,
        *,
        camera_id: str | None = None,
        status: str | None = None,
        incident_type: str | None = None,
        start_after: datetime | None = None,
        start_before: datetime | None = None,
        emergency_vehicle_present: bool | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Incident], int]:
        conditions = self._build_conditions(
            camera_id=camera_id,
            status=status,
            incident_type=incident_type,
            start_after=start_after,
            start_before=start_before,
            emergency_vehicle_present=emergency_vehicle_present,
        )

        count_stmt = select(func.count()).select_from(Incident)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = select(Incident).order_by(Incident.start_time.desc()).offset(offset).limit(limit)
        for cond in conditions:
            stmt = stmt.where(cond)
        rows = (await self.session.execute(stmt)).scalars().all()

        return list(rows), total

    async def count_by_status(self) -> dict[str, int]:
        stmt = select(Incident.status, func.count()).group_by(Incident.status)
        rows = (await self.session.execute(stmt)).all()
        return {status: count for status, count in rows}

    async def count_active(self) -> int:
        stmt = select(func.count()).select_from(Incident).where(Incident.status == "ACTIVE")
        return (await self.session.execute(stmt)).scalar_one()

    async def count_total(self) -> int:
        stmt = select(func.count()).select_from(Incident)
        return (await self.session.execute(stmt)).scalar_one()

    async def recent(self, limit: int = 10) -> list[Incident]:
        stmt = select(Incident).order_by(Incident.start_time.desc()).limit(limit)
        return list((await self.session.execute(stmt)).scalars().all())

    @staticmethod
    def _build_conditions(
        *,
        camera_id: str | None,
        status: str | None,
        incident_type: str | None,
        start_after: datetime | None,
        start_before: datetime | None,
        emergency_vehicle_present: bool | None,
    ) -> list:
        conditions = []
        if camera_id:
            conditions.append(Incident.camera_id == camera_id)
        if status:
            conditions.append(Incident.status == status)
        if incident_type:
            conditions.append(Incident.incident_type == incident_type)
        if start_after:
            conditions.append(Incident.start_time >= start_after)
        if start_before:
            conditions.append(Incident.start_time <= start_before)
        if emergency_vehicle_present is not None:
            conditions.append(Incident.emergency_vehicle_present == emergency_vehicle_present)
        return conditions
