"""HTTP routes for general-purpose AI queries (`/ai/*`), backed by Gemini.

Distinct from `/copilot/*`: the Copilot does structured tool-calling against
CityMind's own domain actions (routing, closures, facilities, incidents).
`/ai/query` is a plain free-text question/answer endpoint with no tool
dispatch — useful for anything that doesn't map to a specific CityMind
action. Both share the same "never 500, never return an empty body" fallback
philosophy: if `GEMINI_API_KEY` is unset or the Gemini call fails, this
still returns a 200 with a clearly-labeled fallback answer.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.schemas.ai import AiQueryRequest, AiQueryResponse
from app.services.gemini_service import query_gemini

router = APIRouter(prefix="/ai", tags=["ai"])


@router.post("/query", response_model=AiQueryResponse)
async def ai_query(payload: AiQueryRequest) -> AiQueryResponse:
    """Free-form AI question answering, powered by Gemini (`GEMINI_API_KEY`).

    Always returns HTTP 200. If no API key is configured or the upstream
    call fails for any reason, `source` is `"fallback"` and `answer` explains
    why, rather than the endpoint raising or returning an empty response.
    """
    result = await query_gemini(payload.prompt, system=payload.system)
    return AiQueryResponse(**result)
