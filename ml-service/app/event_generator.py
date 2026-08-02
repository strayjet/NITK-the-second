"""
Event generation pipeline:

    YOLO detections -> bounding boxes -> vehicle counts -> density
                     -> accident heuristic -> emergency flag -> DetectionEvent

This is the only module that assembles the final output shape. detector.py
and tracker.py only ever produce raw dataclasses; camera_mapper.py only
resolves location metadata. Everything gets stitched together here so the
JSON contract in schemas.py has exactly one place it's built.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.camera_mapper import camera_mapper
from app.config import settings
from app.detector import AccidentDetection
from app.schemas import BoundingBox, DetectionEvent, TrafficDensity, VehicleCounts
from app.tracker import Vehicle

_MOTORCYCLE_LABELS = {"motorcycle", "bike"}
_BICYCLE_LABELS = {"bicycle"}


def _count_vehicles(vehicles: list[Vehicle]) -> VehicleCounts:
    counts = VehicleCounts()
    for vehicle in vehicles:
        if vehicle.label in settings.emergency_classes:
            # Emergency vehicles are reported via `emergency_vehicle`, not folded
            # into the general vehicle-type breakdown.
            continue
        counts.total += 1
        if vehicle.label == "car":
            counts.cars += 1
        elif vehicle.label == "bus":
            counts.buses += 1
        elif vehicle.label == "truck":
            counts.trucks += 1
        elif vehicle.label in _MOTORCYCLE_LABELS:
            counts.motorcycles += 1
        elif vehicle.label in _BICYCLE_LABELS:
            counts.bicycles += 1
    return counts


def classify_density(total_vehicles: int) -> TrafficDensity:
    if total_vehicles >= settings.density_high_threshold:
        return "HIGH"
    if total_vehicles >= settings.density_low_threshold:
        return "MEDIUM"
    return "LOW"


def _to_bounding_boxes(vehicles: list[Vehicle]) -> list[BoundingBox]:
    return [
        BoundingBox(
            label=v.label,
            confidence=round(v.confidence, 3),
            track_id=v.track_id,
            x1=v.bbox[0],
            y1=v.bbox[1],
            x2=v.bbox[2],
            y2=v.bbox[3],
        )
        for v in vehicles
    ]


def build_event(
    camera_id: str,
    vehicles: list[Vehicle],
    accident_detections: list[AccidentDetection],
    emergency_detected: bool,
    frame_index: int | None = None,
) -> DetectionEvent:
    """Assemble one standardized DetectionEvent from a single processed frame."""
    camera_info = camera_mapper.lookup(camera_id)
    vehicle_counts = _count_vehicles(vehicles)
    density = classify_density(vehicle_counts.total)

    accident = bool(accident_detections)
    accident_confidence = max((d.confidence for d in accident_detections), default=0.0)

    return DetectionEvent(
        camera_id=camera_id,
        road_id=camera_info.road,
        lat=camera_info.lat,
        lng=camera_info.lng,
        timestamp=datetime.now(timezone.utc).isoformat(),
        frame_index=frame_index,
        accident=accident,
        confidence=round(accident_confidence, 3),
        vehicles=vehicle_counts,
        traffic_density=density,
        emergency_vehicle=emergency_detected,
        bounding_boxes=_to_bounding_boxes(vehicles),
    )
