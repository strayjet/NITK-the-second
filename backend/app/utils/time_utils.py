"""Timestamp parsing helpers.

The YOLO service emits ISO-8601 timestamp strings. This module centralizes
turning those strings into timezone-aware `datetime` objects so the rest of
the codebase never has to worry about format quirks (e.g. a trailing "Z").
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime (UTC fallback).

    Accepts the common "...Z" suffix (which `datetime.fromisoformat` does not
    understand on its own) by normalizing it to an explicit UTC offset.
    """
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def utcnow() -> datetime:
    """Timezone-aware "now", used as a fallback when no event timestamp is available."""
    return datetime.now(timezone.utc)
