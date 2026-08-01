"""
Accident detection and emergency-vehicle detection.

Ported from the original ai-engine's `accident.py` / `emergency.py` /
`AccidentDetector` (from `main.py`), unchanged in approach:

- AccidentDetector: raw YOLO inference against a dedicated accident-detection
  model checkpoint.
- AccidentMotionEvaluator: gates the raw model output behind two classical-CV
  heuristics (frame-to-frame motion delta + pairwise vehicle bounding-box
  proximity) so a single noisy model frame doesn't fire a false positive on
  its own. This combined signal is what `event_generator.py` treats as the
  authoritative "accident" flag.
- EmergencyVehicleDetector: flags a frame as containing an emergency vehicle
  either because the vehicle model directly classified one (ambulance/fire
  truck classes) or because a detected vehicle's crop looks like an
  ambulance livery (high white-pixel ratio in HSV space).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from app.config import settings
from app.tracker import Vehicle

logger = logging.getLogger(__name__)


@dataclass
class AccidentDetection:
    label: str
    confidence: float
    bbox: tuple[float, float, float, float]


class AccidentDetector:
    """Loads the dedicated accident-detection YOLO checkpoint."""

    def __init__(
        self,
        weights_path: str = settings.accident_weights_path,
        confidence_threshold: float = settings.accident_confidence_threshold,
        device: str = settings.device,
    ):
        self.confidence_threshold = confidence_threshold
        self.device = device
        self.model = None
        self.class_names: dict[int, str] = {}

        try:
            from ultralytics import YOLO

            self.model = YOLO(weights_path)
            self.class_names = self.model.names
            logger.info("accident model loaded: %s", weights_path)
        except Exception as error:  # pragma: no cover - depends on optional ultralytics install
            logger.error("accident model failed to load (%s): %s", weights_path, error)

    @property
    def is_loaded(self) -> bool:
        return self.model is not None

    def detect(self, frame: np.ndarray, min_confidence: float | None = None) -> list[AccidentDetection]:
        if self.model is None:
            return []

        threshold = self.confidence_threshold if min_confidence is None else float(min_confidence)
        results = self.model(frame, device=self.device, verbose=False)

        detections: list[AccidentDetection] = []
        for result in results:
            if result.boxes is None:
                continue
            boxes = result.boxes.xyxy.tolist()
            confidences = result.boxes.conf.tolist()
            classes = result.boxes.cls.tolist()

            for box, conf, cls_idx in zip(boxes, confidences, classes):
                if conf < threshold:
                    continue
                label = str(self.class_names.get(int(cls_idx), "accident")).lower()
                x1, y1, x2, y2 = box
                detections.append(
                    AccidentDetection(
                        label=label,
                        confidence=float(conf),
                        bbox=(float(x1), float(y1), float(x2), float(y2)),
                    )
                )
        return detections


class AccidentMotionEvaluator:
    """
    Confirms (or suppresses) raw model detections using motion + collision
    heuristics. Stateful across frames of the *same* source — instantiate one
    per active stream/video, not one shared globally across sources.
    """

    def __init__(
        self,
        detector: AccidentDetector,
        motion_threshold: int = settings.accident_motion_threshold,
        collision_distance: int = settings.accident_collision_distance,
    ):
        self.detector = detector
        self.motion_threshold = int(motion_threshold)
        self.collision_distance = int(collision_distance)
        self._previous_gray: np.ndarray | None = None

    def _motion_score(self, frame: np.ndarray) -> int:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if self._previous_gray is None or self._previous_gray.shape != gray.shape:
            self._previous_gray = gray
            return 0
        diff = cv2.absdiff(self._previous_gray, gray)
        _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
        score = int(np.sum(thresh))
        self._previous_gray = gray
        return score

    def _collision_detected(self, vehicles: list[Vehicle]) -> bool:
        centers = [(v.cx, v.cy) for v in vehicles]
        for i in range(len(centers)):
            for j in range(i + 1, len(centers)):
                x1, y1 = centers[i]
                x2, y2 = centers[j]
                distance = np.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)
                if distance < self.collision_distance:
                    return True
        return False

    def evaluate(self, frame: np.ndarray, vehicles: list[Vehicle], static_image: bool) -> list[AccidentDetection]:
        raw_detections = self.detector.detect(frame)

        # A single still image has no motion history to compare against, so
        # trust the model output directly.
        if static_image:
            return raw_detections

        model_flagged = len(raw_detections) > 0
        motion_score = self._motion_score(frame)
        collision_detected = self._collision_detected(vehicles)

        if model_flagged and collision_detected and motion_score > self.motion_threshold:
            return raw_detections
        return []


def detect_ambulance_livery(frame: np.ndarray, bbox: tuple[float, float, float, float]) -> bool:
    """Heuristic: ambulances tend to be dominated by white/near-white body panels."""
    x1, y1, x2, y2 = map(int, bbox)
    roi = frame[y1:y2, x1:x2]
    if roi.size == 0:
        return False

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    lower = np.array([0, 0, 200])
    upper = np.array([180, 50, 255])
    mask = cv2.inRange(hsv, lower, upper)
    white_ratio = float(np.sum(mask > 0)) / float(roi.size + 1)
    return white_ratio > settings.ambulance_white_ratio_threshold


class EmergencyVehicleDetector:
    """Combines direct model classification with the ambulance-livery heuristic."""

    def detect(self, frame: np.ndarray, vehicles: list[Vehicle]) -> bool:
        for vehicle in vehicles:
            if vehicle.label in settings.emergency_classes:
                return True

        for vehicle in vehicles:
            if detect_ambulance_livery(frame, vehicle.bbox):
                return True

        return False
