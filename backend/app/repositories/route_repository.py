"""Repository for the `route_analyses` table."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RouteAnalysis


class RouteAnalysisRepository:
    """Async CRUD + query helpers for `RouteAnalysis` rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **fields) -> RouteAnalysis:
        row = RouteAnalysis(**fields)
        self.session.add(row)
        await self.session.flush()
        return row

    async def get(self, route_analysis_id: int) -> RouteAnalysis | None:
        return await self.session.get(RouteAnalysis, route_analysis_id)

    async def list_filtered(
        self,
        *,
        analysis_type: str | None = None,
        status: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[RouteAnalysis], int]:
        conditions = []
        if analysis_type:
            conditions.append(RouteAnalysis.analysis_type == analysis_type)
        if status:
            conditions.append(RouteAnalysis.status == status)

        count_stmt = select(func.count()).select_from(RouteAnalysis)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        total = (await self.session.execute(count_stmt)).scalar_one()

        stmt = select(RouteAnalysis).order_by(RouteAnalysis.created_at.desc()).offset(offset).limit(limit)
        for cond in conditions:
            stmt = stmt.where(cond)
        rows = (await self.session.execute(stmt)).scalars().all()

        return list(rows), total

    async def count_total(self) -> int:
        stmt = select(func.count()).select_from(RouteAnalysis)
        return (await self.session.execute(stmt)).scalar_one()

    async def avg_distance_km(self) -> float:
        stmt = select(func.avg(RouteAnalysis.distance_km)).where(RouteAnalysis.distance_km.is_not(None))
        result = (await self.session.execute(stmt)).scalar_one()
        return round(float(result), 3) if result is not None else 0.0
