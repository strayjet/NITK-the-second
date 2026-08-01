"""
Loads config/camera_map.json and resolves a camera_id to its road metadata
(road name, lat/lng). The detector never needs to know this mapping itself —
it just receives a camera_id, and CameraMapper enriches events with location
context before they're returned.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


class CameraInfo:
    __slots__ = ("camera_id", "road", "lat", "lng")

    def __init__(self, camera_id: str, road: str | None, lat: float | None, lng: float | None):
        self.camera_id = camera_id
        self.road = road
        self.lat = lat
        self.lng = lng


class CameraMapper:
    """Reads camera_map.json once and serves in-memory lookups."""

    def __init__(self, map_path: str | Path = settings.camera_map_path):
        self._map_path = Path(map_path)
        self._cameras: dict[str, CameraInfo] = {}
        self.reload()

    def reload(self) -> None:
        if not self._map_path.exists():
            logger.warning("camera_map.json not found at %s — road enrichment disabled", self._map_path)
            self._cameras = {}
            return

        try:
            raw = json.loads(self._map_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.error("failed to parse camera_map.json: %s", error)
            self._cameras = {}
            return

        cameras: dict[str, CameraInfo] = {}
        for camera_id, meta in raw.items():
            if not isinstance(meta, dict):
                continue
            cameras[camera_id] = CameraInfo(
                camera_id=camera_id,
                road=meta.get("road"),
                lat=meta.get("lat"),
                lng=meta.get("lng"),
            )
        self._cameras = cameras
        logger.info("loaded %d camera mapping(s) from %s", len(cameras), self._map_path)

    def lookup(self, camera_id: str) -> CameraInfo:
        return self._cameras.get(camera_id, CameraInfo(camera_id=camera_id, road=None, lat=None, lng=None))


camera_mapper = CameraMapper()
