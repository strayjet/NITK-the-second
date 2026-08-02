"""
EventAggregator — converts many frame-level `RawDetectionEvent`s into
deduplicated, incident-level `Incident`s.

Design
------
For each camera, the aggregator keeps at most one *active* incident at a
time (this is what prevents duplicate incidents for a single ongoing event).
Incoming events are classified as "accident candidates" if `event.accident`
is true and `event.confidence` meets the configured threshold.

    - A candidate event with no active incident for that camera opens a new
      active incident.
    - A candidate event with an active incident for that camera updates it
      (extends `last_seen_time`, bumps frame/confidence/vehicle stats).
    - A non-candidate event is only meaningful if there's an active
      incident: if the gap since that incident's `last_seen_time` exceeds
      `cooldown_seconds`, the incident is closed. If the gap is still within
      the cooldown window, the incident is left open (a short gap in
      detections shouldn't fragment one real incident into several).

When an incident is closed, it is discarded (not emitted) if its total
duration is shorter than `min_duration_seconds` — this filters out
single-frame noise / spurious detections rather than reporting them as
incidents.

The aggregator is intentionally stateful and safe to share as a singleton
across requests (guarded by an `asyncio.Lock`), since a real deployment
processes a continuous string of frames per camera over many HTTP calls
(e.g. one call per uploaded video clip, or one call per polling interval of
a live stream) and incidents legitimately span multiple calls.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.config import settings
from app.models.detection import RawDetectionEvent, TrafficDensity
from app.models.incident import Incident
from app.utils.time_utils import parse_timestamp

logger = logging.getLogger(__name__)

_DENSITY_RANK: dict[TrafficDensity, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


@dataclass
class EventAggregatorConfig:
    """Tunable behavior of the EventAggregator. See `app.config.settings` for defaults."""

    confidence_threshold: float = 0.6
    cooldown_seconds: float = 5.0
    min_duration_seconds: float = 1.0

    @classmethod
    def from_settings(cls) -> "EventAggregatorConfig":
        return cls(
            confidence_threshold=settings.aggregator_confidence_threshold,
            cooldown_seconds=settings.aggregator_cooldown_seconds,
            min_duration_seconds=settings.aggregator_min_duration_seconds,
        )


@dataclass
class _ActiveIncidentState:
    """Internal, mutable bookkeeping for an incident that hasn't closed yet."""

    incident_id: str
    camera_id: str
    road_id: str | None
    lat: float | None
    lng: float | None

    start_time: datetime
    last_seen_time: datetime

    frame_count: int = 0
    confidence_sum: float = 0.0
    max_confidence: float = 0.0

    emergency_vehicle_present: bool = False
    peak_vehicle_count: int = 0
    traffic_density_at_peak: TrafficDensity = "LOW"

    first_frame_index: int | None = None
    last_frame_index: int | None = None

    def apply(self, event: RawDetectionEvent, timestamp: datetime) -> None:
        self.last_seen_time = timestamp
        self.frame_count += 1
        self.confidence_sum += event.confidence
        self.max_confidence = max(self.max_confidence, event.confidence)
        self.emergency_vehicle_present = self.emergency_vehicle_present or event.emergency_vehicle
        self.last_frame_index = event.frame_index

        if event.vehicles.total >= self.peak_vehicle_count:
            self.peak_vehicle_count = event.vehicles.total
            self.traffic_density_at_peak = event.traffic_density
        elif _DENSITY_RANK.get(event.traffic_density, 0) > _DENSITY_RANK.get(self.traffic_density_at_peak, 0):
            self.traffic_density_at_peak = event.traffic_density

    def to_incident(self, status: str, end_time: datetime | None) -> Incident:
        duration = ((end_time or self.last_seen_time) - self.start_time).total_seconds()
        avg_confidence = self.confidence_sum / self.frame_count if self.frame_count else 0.0

        return Incident(
            incident_id=self.incident_id,
            incident_type="ACCIDENT",
            status=status,  # type: ignore[arg-type]
            camera_id=self.camera_id,
            road_id=self.road_id,
            lat=self.lat,
            lng=self.lng,
            start_time=self.start_time,
            last_seen_time=self.last_seen_time,
            end_time=end_time,
            duration_seconds=max(duration, 0.0),
            frame_count=self.frame_count,
            max_confidence=self.max_confidence,
            avg_confidence=avg_confidence,
            emergency_vehicle_present=self.emergency_vehicle_present,
            peak_vehicle_count=self.peak_vehicle_count,
            traffic_density_at_peak=self.traffic_density_at_peak,
            first_frame_index=self.first_frame_index,
            last_frame_index=self.last_frame_index,
        )


class EventAggregator:
    """Stateful frame-to-incident aggregator, keyed per camera."""

    def __init__(self, config: EventAggregatorConfig | None = None) -> None:
        self.config = config or EventAggregatorConfig.from_settings()
        self._active: dict[str, _ActiveIncidentState] = {}
        self._lock = asyncio.Lock()

    # -- public API ---------------------------------------------------------------

    async def ingest(self, event: RawDetectionEvent) -> Incident | None:
        """Process a single frame-level event.

        Returns the `Incident` if processing this event caused an incident to
        close, otherwise `None`.
        """
        async with self._lock:
            return self._ingest_locked(event)

    async def ingest_many(self, events: list[RawDetectionEvent]) -> list[Incident]:
        """Process a batch of frame-level events (e.g. one video's worth of frames), in order."""
        closed: list[Incident] = []
        async with self._lock:
            for event in events:
                incident = self._ingest_locked(event)
                if incident is not None:
                    closed.append(incident)
        return closed

    async def close_active_incident(self, camera_id: str, at_time: datetime | None = None) -> Incident | None:
        """Force-close the active incident (if any) for a camera.

        Used when a bounded unit of work ends (e.g. a video file or a
        polled stream chunk finishes processing) so an incident that was
        still active doesn't linger forever waiting for a cooldown gap
        that will never arrive within this request.
        """
        async with self._lock:
            state = self._active.pop(camera_id, None)
            if state is None:
                return None
            return self._finalize(state, end_time=at_time or state.last_seen_time)

    async def close_all_active(self) -> list[Incident]:
        """Force-close every currently active incident, across all cameras."""
        async with self._lock:
            closed = []
            for camera_id in list(self._active.keys()):
                state = self._active.pop(camera_id)
                incident = self._finalize(state, end_time=state.last_seen_time)
                if incident is not None:
                    closed.append(incident)
            return closed

    async def get_active_incidents(self, camera_id: str | None = None) -> list[Incident]:
        """Snapshot currently open incidents (optionally filtered to one camera)."""
        async with self._lock:
            states = self._active.values() if camera_id is None else (
                [self._active[camera_id]] if camera_id in self._active else []
            )
            return [state.to_incident(status="ACTIVE", end_time=None) for state in states]

    # -- internals (must be called while holding self._lock) ------------------------

    def _ingest_locked(self, event: RawDetectionEvent) -> Incident | None:
        timestamp = parse_timestamp(event.timestamp)
        is_candidate = event.accident and event.confidence >= self.config.confidence_threshold

        state = self._active.get(event.camera_id)

        if is_candidate:
            if state is None:
                state = _ActiveIncidentState(
                    incident_id=str(uuid.uuid4()),
                    camera_id=event.camera_id,
                    road_id=event.road_id,
                    lat=event.lat,
                    lng=event.lng,
                    start_time=timestamp,
                    last_seen_time=timestamp,
                    first_frame_index=event.frame_index,
                )
                self._active[event.camera_id] = state
                logger.info(
                    "opened incident %s on camera %s at %s",
                    state.incident_id,
                    event.camera_id,
                    timestamp.isoformat(),
                )
            state.apply(event, timestamp)
            return None

        # Not a candidate frame: only relevant if there's an active incident.
        if state is None:
            return None

        gap_seconds = (timestamp - state.last_seen_time).total_seconds()
        if gap_seconds < self.config.cooldown_seconds:
            # Still within the cooldown grace period — a brief gap in
            # positive detections shouldn't fragment one real incident.
            return None

        # Cooldown exceeded: close the incident.
        del self._active[event.camera_id]
        return self._finalize(state, end_time=state.last_seen_time)

    def _finalize(self, state: _ActiveIncidentState, end_time: datetime) -> Incident | None:
        incident = state.to_incident(status="CLOSED", end_time=end_time)

        if incident.duration_seconds < self.config.min_duration_seconds:
            logger.info(
                "discarding incident %s on camera %s: duration %.2fs below minimum %.2fs",
                incident.incident_id,
                incident.camera_id,
                incident.duration_seconds,
                self.config.min_duration_seconds,
            )
            return None

        logger.info(
            "closed incident %s on camera %s (%.2fs, %d frames, max_confidence=%.2f)",
            incident.incident_id,
            incident.camera_id,
            incident.duration_seconds,
            incident.frame_count,
            incident.max_confidence,
        )
        return incident
