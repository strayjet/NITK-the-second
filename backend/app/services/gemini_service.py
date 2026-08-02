"""
GeminiService — general-purpose free-text AI query used by `/ai/query`.

This is deliberately separate from `CopilotService` (which does structured
tool/function-calling against CityMind's own domain tools). `/ai/query` is a
plain "ask Gemini a question and get a text answer back" endpoint for
free-form queries that don't map to a specific CityMind action.

Like the Copilot's LLM backend, this never raises on failure: if
`GEMINI_API_KEY` is unset, or the Gemini API call fails/times out/rate-limits,
it returns a clearly-labeled fallback response instead of a 500 or an empty
body, so the frontend never has to render an empty screen.
"""

from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
)

_FALLBACK_MESSAGE = (
    "AI query is currently unavailable (no GEMINI_API_KEY configured, or the Gemini API request "
    "failed). This is a graceful fallback response, not an error — try again later or configure "
    "GEMINI_API_KEY, or use /copilot/chat for routing, closures, facilities, and incidents, which "
    "works fully offline via its rule-based classifier."
)


async def query_gemini(prompt: str, *, system: str | None = None) -> dict[str, object]:
    """Send `prompt` to Gemini and return a plain-text answer.

    Always returns a dict with `answer`, `source` ("gemini" | "fallback"), and `error`
    (populated only when `source == "fallback"` because of a real failure, as opposed to a
    missing API key).
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return {"answer": _FALLBACK_MESSAGE, "source": "fallback", "error": None}

    payload: dict[str, object] = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                _GEMINI_URL,
                headers={"content-type": "application/json"},
                params={"key": api_key},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates") or []
        parts = candidates[0].get("content", {}).get("parts", []) if candidates else []
        text = "".join(p.get("text", "") for p in parts).strip()
        if not text:
            # Gemini responded but with no usable text (e.g. blocked by safety filters) —
            # still a "no crash, no empty response" situation, so fall back clearly.
            finish_reason = candidates[0].get("finishReason") if candidates else None
            return {
                "answer": _FALLBACK_MESSAGE,
                "source": "fallback",
                "error": f"Gemini returned no text (finish_reason={finish_reason})",
            }
        return {"answer": text, "source": "gemini", "error": None}

    except Exception as exc:  # noqa: BLE001 - never let /ai/query 500 or return empty
        logger.exception("Gemini query failed")
        return {"answer": _FALLBACK_MESSAGE, "source": "fallback", "error": str(exc)}
