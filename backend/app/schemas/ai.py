"""Request/response schemas for the general-purpose `/ai/*` endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AiQueryRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=4000, description="Free-form natural-language prompt.")
    system: str | None = Field(
        default=None,
        description="Optional system instruction to steer the model (e.g. persona, output format).",
    )


class AiQueryResponse(BaseModel):
    answer: str
    source: str = Field(..., description="'gemini' if the live API answered, 'fallback' otherwise.")
    error: str | None = Field(default=None, description="Populated only when `source == 'fallback'` due to a real failure.")
