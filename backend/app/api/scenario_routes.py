"""HTTP route for the scenario simulator (`POST /simulate/scenario`)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.repositories.facility_repository import FacilityRepository
from app.schemas.scenario import ScenarioRequest, ScenarioResponse
from app.services.city_service import CityLogicComputationError, CityLogicNotReadyError, CityService
from app.services.scenario_service import ScenarioService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["simulate"])


def get_city_service(request: Request) -> CityService:
    return request.app.state.city_service


@router.post("/simulate/scenario", response_model=ScenarioResponse)
async def simulate_scenario(
    payload: ScenarioRequest,
    city_service: CityService = Depends(get_city_service),
    db: AsyncSession = Depends(get_db),
) -> ScenarioResponse:
    """
    Simulate a road closure, facility addition, facility removal, or facility
    relocation, and report route/ETA changes plus citywide coverage impact.
    """
    facility_repo = FacilityRepository(db)
    service = ScenarioService(city_service=city_service, facility_repo=facility_repo)
    try:
        return await service.simulate(payload)
    except CityLogicNotReadyError as exc:
        raise HTTPException(status_code=503, detail=f"city routing engine unavailable: {exc}") from exc
    except CityLogicComputationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("unexpected error in scenario simulation")
        raise HTTPException(status_code=500, detail="internal error computing scenario simulation") from exc
