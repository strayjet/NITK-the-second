"""
RealtimeHub — the real-time push layer for citymind-backend.

Bridges server-side events (new/closed incidents, active-incident snapshots,
dashboard cache invalidations) to connected WebSocket clients.

Two modes, chosen automatically:

  - Single-process (no REDIS_URL configured): events are broadcast directly
    to every WebSocket connected to *this* process. Zero extra
    infrastructure required — this is the default and works out of the box.

  - Multi-process (REDIS_URL configured): events are published to a Redis
    Pub/Sub channel, and every process (including the one that published)
    also runs a subscriber loop that fans incoming messages out to its own
    locally-connected WebSocket clients. This lets `/ws/live` clients on any
    backend replica see incidents produced by detections handled on any
    other replica.

This module never raises out of `broadcast()` — a dead/slow client, or Redis
being briefly unavailable, must never break the request that generated the
event.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

REDIS_CHANNEL = "citymind:realtime"


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


class RealtimeHub:
    """Manages connected WebSocket clients and fans out JSON events to them."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._redis = None
        self._pubsub = None
        self._subscriber_task: asyncio.Task | None = None

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if not self._redis_url:
            logger.info("RealtimeHub starting in single-process mode (no REDIS_URL set)")
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            self._pubsub = self._redis.pubsub()
            await self._pubsub.subscribe(REDIS_CHANNEL)
            self._subscriber_task = asyncio.create_task(self._subscriber_loop())
            logger.info("RealtimeHub connected to Redis at %s (multi-process mode)", self._redis_url)
        except Exception as exc:  # noqa: BLE001 - realtime fan-out must never crash startup
            logger.warning(
                "RealtimeHub could not connect to Redis (%s) — falling back to single-process mode: %s",
                self._redis_url,
                exc,
            )
            self._redis = None
            self._pubsub = None

    async def stop(self) -> None:
        if self._subscriber_task is not None:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(REDIS_CHANNEL)
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def _subscriber_loop(self) -> None:
        assert self._pubsub is not None
        try:
            async for message in self._pubsub.listen():
                if message is None or message.get("type") != "message":
                    continue
                await self._local_broadcast(message["data"])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("RealtimeHub Redis subscriber loop stopped unexpectedly: %s", exc)

    # -- connection management -------------------------------------------

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
        logger.info("WebSocket client connected (total=%d)", len(self._connections))

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)
        logger.info("WebSocket client disconnected (total=%d)", len(self._connections))

    # -- broadcast --------------------------------------------------------

    async def broadcast(self, event_type: str, payload: Any) -> None:
        """Publish an event to every connected client (and Redis, if configured).

        `event_type` is a short dotted string like "incident.closed",
        "incident.active", or "dashboard.invalidated". Never raises.
        """
        message = json.dumps({"type": event_type, "data": payload}, default=_json_default)

        if self._redis is not None:
            try:
                await self._redis.publish(REDIS_CHANNEL, message)
                return  # the subscriber loop will deliver it locally too
            except Exception as exc:  # noqa: BLE001
                logger.warning("RealtimeHub failed to publish to Redis, falling back to local broadcast: %s", exc)

        await self._local_broadcast(message)

    async def _local_broadcast(self, message: str) -> None:
        async with self._lock:
            connections = list(self._connections)

        dead: list[WebSocket] = []
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001 - a broken client must not affect the others
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)
