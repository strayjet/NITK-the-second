"""
Small, dependency-light helpers shared across the service:
- decoding raw image bytes into an OpenCV frame
- opening a video/RTSP/webcam source
- iterating a capture with frame-skipping and a hard frame cap

Kept deliberately free of any YOLO/tracking logic — this module only knows
about pixels, not detections.
"""

from __future__ import annotations

import logging
from typing import Generator

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def decode_image_bytes(data: bytes) -> np.ndarray | None:
    """Decode raw image bytes (e.g. from an UploadFile) into a BGR frame."""
    arr = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if frame is None:
        logger.error("failed to decode image bytes (%d bytes received)", len(data))
    return frame


def open_video_source(source: str | int) -> cv2.VideoCapture:
    """
    Open any source cv2.VideoCapture understands: a local file path, an
    http(s) URL, or an rtsp:// URL. Webcam device indices are opened via
    open_webcam() instead, since they need OS-specific handling.
    """
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        logger.error("failed to open video source: %s", source)
    return cap


def open_webcam(device_index: int) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(device_index)
    if not cap.isOpened():
        logger.error("failed to open webcam device index: %s", device_index)
    return cap


def iter_frames(
    cap: cv2.VideoCapture,
    frame_skip: int = 1,
    max_frames: int | None = None,
) -> Generator[tuple[int, np.ndarray], None, None]:
    """
    Yield (frame_index, frame) pairs from an open capture, keeping only every
    `frame_skip`-th frame and stopping after `max_frames` yielded frames
    (None = read until the source ends). Always releases the capture when done.
    """
    frame_idx = 0
    yielded = 0
    try:
        while True:
            if max_frames is not None and yielded >= max_frames:
                break

            ok, frame = cap.read()
            if not ok or frame is None:
                break

            frame_idx += 1
            if frame_skip > 1 and (frame_idx % frame_skip != 0):
                continue

            yielded += 1
            yield frame_idx, frame
    finally:
        cap.release()
