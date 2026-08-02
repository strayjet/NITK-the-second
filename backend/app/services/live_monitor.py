"""
LiveMonitor — optional, continuous ML -> backend event pipeline.

Every `/aggregate/*` HTTP route already does real detection -> aggregation
-> persistence -> real-time broadcast for a single request-response unit of
work (one image, one video, one bounded stream poll). LiveMonitor is the
always-on counterpart: for every camera configured in
`settings.live_camera_sources`, it repeatedly calls
`YoloClient.detect_stream(...)` against that camera's real source URL (an
RTSP/HTTP stream citymind-yolo-service can open), feeds the resulting
frame-level detections through the same `EventAggregator`, persists them,
and pushes the result over the `RealtimeHub`.

Deliberately does nothing when no camera sources are configured (the
default) — there is no synthetic/mock data generation here, only a real
polling loop over real, user-supplied stream URLs.
"""

from __future__ import annotations

import asyncio
import logging

from app.db.session import session_scope
from app.models.incident import AggregationResult
from app.repositories.detection_repository import DetectionEventRepository
from app.repositories.incident_repository import IncidentRepository
from app.services.event_aggregator import EventAggregator
from app.services.realtime import RealtimeHub
from app.services.yolo_client import YoloClient, YoloClientError
from app.utils.cache import dashboard_cache

logger = logging.getLogger(__name__)


class LiveCameraSource:
    def __init__(
        self,
        camera_id: str,
        source_url: str,
        poll_interval_seconds: float = 10.0,
        max_frames: int | None = None,
    ) -> None:
        self.camera_id = camera_id
        self.source_url = source_url
        self.poll_interval_seconds = poll_interval_seconds
        self.max_frames = max_frames


class LiveMonitor:
    """Owns one background asyncio task per configured live camera source."""

    def __init__(
        self,
        yolo_client: YoloClient,
        aggregator: EventAggregator,
        realtime_hub: RealtimeHub,
        sources: list[LiveCameraSource],
    ) -> None:
        self._yolo_client = yolo_client
        self._aggregator = aggregator
        self._realtime_hub = realtime_hub
        self._sources = sources
        self._tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if not self._sources:
            logger.info("LiveMonitor: no live_camera_sources configured — continuous polling disabled")
            return
        for source in self._sources:
            task = asyncio.create_task(self._poll_loop(source), name=f"live-monitor-{source.camera_id}")
            self._tasks.append(task)
        logger.info("LiveMonitor: started polling for %d camera(s)", len(self._tasks))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    async def _poll_loop(self, source: LiveCameraSource) -> None:
        logger.info("LiveMonitor: polling camera %s at %s", source.camera_id, source.source_url)
        while True:
            try:
                await self._poll_once(source)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad poll must not kill the loop
                logger.warning("LiveMonitor: poll failed for camera %s: %s", source.camera_id, exc)

            try:
                await asyncio.sleep(source.poll_interval_seconds)
            except asyncio.CancelledError:
                raise

    async def _poll_once(self, source: LiveCameraSource) -> None:
        from app.models.detection import RawVideoDetectionResponse

        try:
            raw = await self._yolo_client.detect_stream(
                source_url=source.source_url,
                camera_id=source.camera_id,
                max_frames=source.max_frames,
            )
        except YoloClientError as exc:
            logger.warning("LiveMonitor: YOLO service error for camera %s: %s", source.camera_id, exc)
            return

        parsed = RawVideoDetectionResponse(**raw)
        if not parsed.events:
            return

        closed = await self._aggregator.ingest_many(parsed.events)
        active = await self._aggregator.get_active_incidents(camera_id=source.camera_id)

        async with session_scope() as db:
            try:
                incident_id = closed[-1].incident_id if closed else None
                await DetectionEventRepository(db).create_many_from_schema(parsed.events, incident_id=incident_id)
                incident_repo = IncidentRepository(db)
                for incident in closed:
                    await incident_repo.upsert_from_schema(incident)
            except Exception:  # noqa: BLE001 - persistence must not break the polling loop
                logger.exception("LiveMonitor: failed to persist events for camera %s", source.camera_id)

        if closed:
            await dashboard_cache.invalidate()

        result = AggregationResult(
            camera_id=source.camera_id,
            road_id=parsed.road_id,
            frames_processed=parsed.frames_processed,
            incidents=closed,
            active_incidents=active,
            incident_count=len(closed),
            active_incident_count=len(active),
        )

        for incident in closed:
            await self._realtime_hub.broadcast("incident.closed", incident.model_dump())
        if active:
            await self._realtime_hub.broadcast(
                "camera.active_incidents", {"camera_id": source.camera_id, "incidents": [i.model_dump() for i in active]}
            )
        await self._realtime_hub.broadcast("aggregation.poll", result.model_dump())
