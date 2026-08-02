"""
HTTP routes for the frontend-facing dashboard: aggregated summaries plus
filtered, paginated listings over incidents, facilities, congestion, and
route analyses. Every response is a plain, already-shaped Pydantic model —
no ORM objects or raw DB rows ever leave this layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.repositories.detection_repository import DetectionEventRepository
from app.repositories.facility_repository import FacilityRepository
from app.repositories.incident_repository import IncidentRepository
from app.repositories.route_repository import RouteAnalysisRepository
from app.schemas.dashboard import (
    CongestionResponse,
    CongestionSegment,
    DashboardSummaryResponse,
    FacilitySummary,
    IncidentSummary,
    RouteAnalysisSummary,
)
from app.schemas.pagination import Page
from app.utils.cache import dashboard_cache

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _page_size(page_size: int) -> int:
    return min(page_size, settings.dashboard_max_page_size)


@router.get("/summary", response_model=DashboardSummaryResponse)
async def dashboard_summary(db: AsyncSession = Depends(get_db)) -> DashboardSummaryResponse:
    """High-level counts and aggregates for the dashboard landing page. Cached briefly."""

    async def _compute() -> DashboardSummaryResponse:
        incident_repo = IncidentRepository(db)
        facility_repo = FacilityRepository(db)
        detection_repo = DetectionEventRepository(db)
        route_repo = RouteAnalysisRepository(db)

        incidents_by_status = await incident_repo.count_by_status()
        total_incidents = sum(incidents_by_status.values())
        active_incidents = incidents_by_status.get("ACTIVE", 0)
        closed_incidents = incidents_by_status.get("CLOSED", 0)

        total_facilities = await facility_repo.count_total()
        facilities_by_type = await facility_repo.count_by_type()
        total_detection_events = await detection_repo.count_total()
        total_route_analyses = await route_repo.count_total()
        avg_route_distance_km = await route_repo.avg_distance_km()

        return DashboardSummaryResponse(
            total_incidents=total_incidents,
            active_incidents=active_incidents,
            closed_incidents=closed_incidents,
            total_facilities=total_facilities,
            total_detection_events=total_detection_events,
            total_route_analyses=total_route_analyses,
            avg_route_distance_km=avg_route_distance_km,
            incidents_by_status=incidents_by_status,
            facilities_by_type=facilities_by_type,
            generated_at=datetime.now(timezone.utc),
            cached=False,
        )

    result, was_cached = await dashboard_cache.get_or_set(
        "dashboard:summary", _compute, ttl_seconds=settings.dashboard_cache_ttl_seconds
    )
    if was_cached:
        result = result.model_copy(update={"cached": True})
    return result


@router.get("/incidents", response_model=Page[IncidentSummary])
async def dashboard_incidents(
    db: AsyncSession = Depends(get_db),
    camera_id: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern="^(ACTIVE|CLOSED)$"),
    incident_type: str | None = Query(default=None),
    emergency_vehicle_present: bool | None = Query(default=None),
    start_after: datetime | None = Query(default=None),
    start_before: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.dashboard_default_page_size, ge=1),
) -> Page[IncidentSummary]:
    """Filterable, paginated incident listing."""
    page_size = _page_size(page_size)
    repo = IncidentRepository(db)
    rows, total = await repo.list_filtered(
        camera_id=camera_id,
        status=status,
        incident_type=incident_type,
        start_after=start_after,
        start_before=start_before,
        emergency_vehicle_present=emergency_vehicle_present,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    items = [IncidentSummary.model_validate(row) for row in rows]
    return Page.build(items=items, page=page, page_size=page_size, total_items=total)


@router.get("/facilities", response_model=Page[FacilitySummary])
async def dashboard_facilities(
    db: AsyncSession = Depends(get_db),
    facility_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.dashboard_default_page_size, ge=1),
) -> Page[FacilitySummary]:
    """Filterable, paginated facility listing."""
    page_size = _page_size(page_size)
    repo = FacilityRepository(db)
    rows, total = await repo.list_filtered(
        facility_type=facility_type,
        status=status,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    items = [FacilitySummary.model_validate(row) for row in rows]
    return Page.build(items=items, page=page, page_size=page_size, total_items=total)


@router.get("/congestion", response_model=CongestionResponse)
async def dashboard_congestion(
    db: AsyncSession = Depends(get_db),
    since: datetime | None = Query(default=None, description="Only include events at/after this time"),
    limit: int = Query(default=50, ge=1, le=200),
) -> CongestionResponse:
    """Per-road congestion rollup (avg vehicle count, density score, accident count). Cached briefly."""
    cache_key = f"dashboard:congestion:{since.isoformat() if since else 'all'}:{limit}"

    async def _compute() -> CongestionResponse:
        repo = DetectionEventRepository(db)
        rows = await repo.congestion_by_road(since=since, limit=limit)
        return CongestionResponse(
            segments=[CongestionSegment(**row) for row in rows],
            generated_at=datetime.now(timezone.utc),
            cached=False,
        )

    result, was_cached = await dashboard_cache.get_or_set(
        cache_key, _compute, ttl_seconds=settings.dashboard_cache_ttl_seconds
    )
    if was_cached:
        result = result.model_copy(update={"cached": True})
    return result


@router.get("/routes", response_model=Page[RouteAnalysisSummary])
async def dashboard_routes(
    db: AsyncSession = Depends(get_db),
    analysis_type: str | None = Query(default=None, pattern="^(ROUTE|CLOSURE)$"),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=settings.dashboard_default_page_size, ge=1),
) -> Page[RouteAnalysisSummary]:
    """Filterable, paginated route/closure analysis history."""
    page_size = _page_size(page_size)
    repo = RouteAnalysisRepository(db)
    rows, total = await repo.list_filtered(
        analysis_type=analysis_type,
        status=status,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    items = [RouteAnalysisSummary.model_validate(row) for row in rows]
    return Page.build(items=items, page=page, page_size=page_size, total_items=total)
