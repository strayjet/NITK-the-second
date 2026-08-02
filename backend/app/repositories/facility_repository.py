"""Repository for the `facilities` table."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Facility


class FacilityRepository:
    """Async CRUD + query helpers for `Facility` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        node_id: str,
        lat: float,
        lon: float,
        cost: float,
        n_candidates: int,
        candidate_costs: dict | None = None,
        facility_type: str = "GENERIC",
        name: str | None = None,
        status: str = "RECOMMENDED",
        min_spacing_km: float | None = None,
        existing_facilities_considered: int | None = None,
    ) -> Facility:
        facility = Facility(
            node_id=node_id,
            lat=lat,
            lon=lon,
            cost=cost,
            n_candidates=n_candidates,
            candidate_costs=candidate_costs,
            facility_type=facility_type,
            name=name,
            status=status,
            min_spacing_km=min_spacing_km,
            existing_facilities_considered=existing_facilities_considered,
        )
        self.session.add(facility)
        await self.session.flush()
        return facility

    async def get(self, facility_id: int) -> Facility | None:
        return await self.session.get(Facility, facility_id)

    async def list_filtered(
        self,
        *,
        facility_type: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Facility], int]:
        conditions = []
        if facility_type:
            conditions.append(Facility.facility_type == facility_type)
        if status:
            conditions.append(Facility.status == status)

        count_stmt = select(func.count()).select_from(Facility)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = select(Facility).order_by(Facility.created_at.desc()).offset(offset).limit(limit)
        for cond in conditions:
            stmt = stmt.where(cond)
        rows = (await self.session.execute(stmt)).scalars().all()

        return list(rows), total

    async def count_total(self) -> int:
        stmt = select(func.count()).select_from(Facility)
        return (await self.session.execute(stmt)).scalar_one()

    async def count_by_type(self) -> dict[str, int]:
        stmt = select(Facility.facility_type, func.count()).group_by(Facility.facility_type)
        rows = (await self.session.execute(stmt)).all()
        return {ftype: count for ftype, count in rows}
