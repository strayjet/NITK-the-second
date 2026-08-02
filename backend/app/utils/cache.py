"""
Minimal async-safe in-memory TTL cache.

Dashboard aggregation queries (summary counts, congestion rollups) are
relatively expensive and read-mostly, so a short TTL cache meaningfully cuts
database load under polling dashboards without requiring an external cache
service. Not distributed — fine for a single-process deployment; swap for
Redis if the backend is ever horizontally scaled.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable


class TTLCache:
    def __init__(self, default_ttl_seconds: float = 30.0) -> None:
        self._default_ttl = default_ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()

    async def get_or_set(
        self,
        key: str,
        factory: Callable[[], Awaitable[Any]],
        ttl_seconds: float | None = None,
    ) -> tuple[Any, bool]:
        """Return (value, was_cached). Computes and stores via `factory` on a miss/expiry."""
        now = time.monotonic()
        async with self._lock:
            cached = self._store.get(key)
            if cached is not None and cached[0] > now:
                return cached[1], True

        value = await factory()

        async with self._lock:
            expires_at = now + (ttl_seconds if ttl_seconds is not None else self._default_ttl)
            self._store[key] = (expires_at, value)

        return value, False

    async def invalidate(self, key: str | None = None) -> None:
        async with self._lock:
            if key is None:
                self._store.clear()
            else:
                self._store.pop(key, None)


dashboard_cache = TTLCache()
