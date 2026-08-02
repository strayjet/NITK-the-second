"""
Centralized configuration for citymind-yolo-service.

Every tunable value lives here and is overridable via environment variables
(or a `.env` file loaded at process start). Nothing else in the codebase
should read `os.environ` directly.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Service metadata -------------------------------------------------
    service_name: str = "citymind-yolo-service"
    host: str = "0.0.0.0"
    port: int = 8001
    log_level: str = "INFO"

    # --- Device / inference -------------------------------------------------
    device: str = "cpu"  # "cpu" or "cuda", or a specific cuda index e.g. "cuda:0"
    frame_skip: int = 2  # process every Nth frame for video/stream/webcam sources

    # --- Vehicle detection + tracking (YOLOv8 + ByteTrack) ------------------
    yolo_weights_path: str = str(APP_ROOT / "weights" / "yolov8n.pt")
    yolo_confidence_threshold: float = 0.3
    yolo_tracker_config: str = "bytetrack.yaml"
    vehicle_classes: set[str] = {"car", "truck", "bus", "motorcycle", "bicycle"}
    emergency_classes: set[str] = {"ambulance", "fire truck", "fire_truck"}

    # --- Accident detection ---------------------------------------------
    accident_weights_path: str = str(APP_ROOT / "weights" / "accident_best.pt")
    accident_confidence_threshold: float = 0.8
    accident_motion_threshold: int = 500_000
    accident_collision_distance: int = 50
    accident_min_streak: int = 1  # consecutive positive frames required before a stream reports an accident

    # --- Emergency-vehicle heuristic (ambulance livery check) ------------
    ambulance_white_ratio_threshold: float = 0.35

    # --- Traffic density classification -----------------------------------
    density_low_threshold: int = 8
    density_high_threshold: int = 20

    # --- Camera mapping ----------------------------------------------------
    camera_map_path: str = str(APP_ROOT / "config" / "camera_map.json")
    default_camera_id: str = "UNKNOWN_CAM"

    # --- Source ingestion limits (video / stream / webcam endpoints) -------
    max_frames_per_request: int = 300  # hard cap so a single request can't run forever
    webcam_read_timeout_sec: float = 5.0


settings = Settings()
