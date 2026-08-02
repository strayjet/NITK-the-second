"""HTTP route for the heatmap engine (`GET /heatmap`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.heatmap import HeatmapResponse
from app.services.city_service import CityService
from app.services.heatmap_service import HeatmapService

router = APIRouter(tags=["heatmap"])


def get_city_service(request: Request) -> CityService:
    return request.app.state.city_service


@router.get("/heatmap", response_model=HeatmapResponse)
async def get_heatmap(
    layers: str = Query(
        default="population,accidents,closures",
        description="Comma-separated subset of: population, accidents, closures",
    ),
    limit: int = Query(default=500, ge=1, le=5000),
    city_service: CityService = Depends(get_city_service),
    db: AsyncSession = Depends(get_db),
) -> HeatmapResponse:
    """Population density, accident frequency, and closure-impact GeoJSON layers, in one call."""
    requested = [layer.strip().lower() for layer in layers.split(",") if layer.strip()]
    valid = {"population", "accidents", "closures"}
    requested = [layer for layer in requested if layer in valid] or list(valid)

    service = HeatmapService(city_service=city_service, db=db)
    return await service.build(layers=requested, limit=limit)
