"""HTTP routes for the CityMind AI Copilot (`/copilot/*`)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.copilot import CopilotChatRequest, CopilotChatResponse
from app.services.city_service import CityService
from app.services.copilot_service import TOOL_SCHEMAS, CopilotService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/copilot", tags=["copilot"])


def get_city_service(request: Request) -> CityService:
    return request.app.state.city_service


@router.get("/tools")
async def list_tools() -> dict:
    """Introspection endpoint: the tool/function schemas the copilot can call."""
    return {"tools": TOOL_SCHEMAS}


@router.post("/chat", response_model=CopilotChatResponse)
async def copilot_chat(
    payload: CopilotChatRequest,
    city_service: CityService = Depends(get_city_service),
    db: AsyncSession = Depends(get_db),
) -> CopilotChatResponse:
    """
    Natural-language entrypoint to every CityMind capability: route planning,
    facility recommendation, closure simulation, incident lookup, and
    emergency response analysis. Internally dispatches to the same tools as
    the dedicated `/city/*`, `/dashboard/*`, and `/simulate/*` endpoints, so
    results are always consistent with using those endpoints directly.
    """
    service = CopilotService(city_service=city_service, db=db)
    return await service.chat(message=payload.message, context=payload.context)
