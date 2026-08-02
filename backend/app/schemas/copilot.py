"""Request/response schemas for `/copilot/*` endpoints."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CopilotIntent = Literal[
    "ROUTE_PLANNING",
    "FACILITY_RECOMMENDATION",
    "CLOSURE_SIMULATION",
    "INCIDENT_LOOKUP",
    "EMERGENCY_RESPONSE",
    "UNKNOWN",
]


class CopilotChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="Free-form natural-language request.")
    session_id: str | None = Field(default=None, description="Optional client-generated conversation id.")
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured hints (e.g. {'origin': [lat, lon], 'destination': [lat, lon]}) "
        "that override/augment what is parsed from `message`.",
    )


class ToolCall(BaseModel):
    """A single tool/function invocation the copilot made while answering."""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None


class CopilotChatResponse(BaseModel):
    intent: CopilotIntent
    reply: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    data: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
