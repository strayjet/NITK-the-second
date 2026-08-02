"""
Vehicle detection and multi-object tracking.

Wraps a single Ultralytics YOLOv8 model and its built-in ByteTrack tracker
(`tracker="bytetrack.yaml"`, shipped with `ultralytics`). This is the only
module that touches the vehicle-detection model — everything downstream
consumes the plain `Vehicle` dataclass this module produces.

Ported and cleaned up from the original ai-engine's `YoloWrapper`, which
called `.track()` without pinning a tracker (defaulting to BoT-SORT). We
pin `bytetrack.yaml` explicitly so tracking behavior is ByteTrack as
intended, not whatever Ultralytics' default happens to be.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class Vehicle:
    label: str
    confidence: float
    track_id: int | None
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2
    cx: int
    cy: int


class VehicleTracker:
    """Loads the vehicle YOLO model once and exposes detect()/track()."""

    def __init__(
        self,
        weights_path: str = settings.yolo_weights_path,
        confidence_threshold: float = settings.yolo_confidence_threshold,
        tracker_config: str = settings.yolo_tracker_config,
        device: str = settings.device,
    ):
        self.confidence_threshold = confidence_threshold
        self.tracker_config = tracker_config
        self.device = device
        self.model = None
        self.class_names: dict[int, str] = {}

        try:
            from ultralytics import YOLO

            self.model = YOLO(weights_path)
            self.class_names = self.model.names
            logger.info("vehicle model loaded: %s", weights_path)
        except Exception as error:  # pragma: no cover - depends on optional ultralytics install
            logger.error("vehicle model failed to load (%s): %s", weights_path, error)

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def reset_tracking_state(self) -> None:
        """
        Clear ByteTrack's internal identity state before starting a new
        video/stream/webcam session. The model instance is shared across
        requests for performance, but track IDs must not leak between
        unrelated sources — without this, a new video would inherit track
        IDs (and therefore vehicle "memory") from whatever was processed
        before it.
        """
        if self.model is None:
            return
        try:
            predictor = getattr(self.model, "predictor", None)
            trackers = getattr(predictor, "trackers", None) if predictor else None
            if trackers:
                for t in trackers:
                    if hasattr(t, "reset"):
                        t.reset()
            else:
                # No tracker initialized yet, or Ultralytics version doesn't
                # expose .trackers directly — forcing predictor rebuild on
                # the next .track() call achieves the same reset.
                self.model.predictor = None
        except Exception as error:  # pragma: no cover - defensive, version-dependent internals
            logger.warning("could not reset tracker state cleanly: %s", error)
            self.model.predictor = None

    def detect(self, frame: np.ndarray) -> list[Vehicle]:

        """Single-frame detection, no identity tracking (use for isolated images)."""
        return self._run(frame, persist=False)

    def track(self, frame: np.ndarray, persist: bool = True) -> list[Vehicle]:
        """Detection with ByteTrack identity tracking across calls (use for video/stream)."""
        return self._run(frame, persist=persist)

    def _run(self, frame: np.ndarray, persist: bool) -> list[Vehicle]:
        if self.model is None:
            return []

        if persist:
            results = self.model.track(
                frame,
                persist=True,
                tracker=self.tracker_config,
                device=self.device,
                verbose=False,
            )
        else:
            results = self.model(frame, device=self.device, verbose=False)

        vehicles: list[Vehicle] = []
        allowed_labels = settings.vehicle_classes | settings.emergency_classes

        for result in results:
            if result.boxes is None:
                continue

            boxes = result.boxes.xyxy.tolist()
            confidences = result.boxes.conf.tolist()
            classes = result.boxes.cls.tolist()
            track_ids = result.boxes.id.tolist() if result.boxes.id is not None else [None] * len(boxes)

            for box, conf, cls_idx, track_id in zip(boxes, confidences, classes, track_ids):
                if conf < self.confidence_threshold:
                    continue

                label = str(self.class_names.get(int(cls_idx), "unknown")).lower()
                if label not in allowed_labels:
                    continue

                x1, y1, x2, y2 = box
                vehicles.append(
                    Vehicle(
                        label=label,
                        confidence=float(conf),
                        track_id=int(track_id) if track_id is not None else None,
                        bbox=(float(x1), float(y1), float(x2), float(y2)),
                        cx=int((x1 + x2) / 2),
                        cy=int((y1 + y2) / 2),
                    )
                )

        return vehicles
