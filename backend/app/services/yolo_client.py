"""
YoloClient — the only place in citymind-backend that knows how to talk to
citymind-yolo-service over HTTP.

Responsibilities (and only these — no business logic lives here):
    - Call the YOLO service's endpoints (/health, /detect/image,
      /detect/video, /detect/stream, /detect/webcam).
    - Upload images and videos as multipart form data.
    - Connect to live streams (RTSP/HTTP) by forwarding a source URL.
    - Return parsed JSON (as plain dicts) to the caller.
    - Handle connection failures, timeouts, and transient 5xx errors with
      bounded exponential-backoff retries.
    - Log every outbound call, retry, and failure.

This module does not interpret detection results, does not build
incidents, and does not know anything about the Event Aggregator — that
logic lives in `app.services.event_aggregator`.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings

logger = logging.getLogger(__name__)


class YoloClientError(Exception):
    """Base class for all errors raised by YoloClient."""


class YoloConnectionError(YoloClientError):
    """Raised when the YOLO service could not be reached at all (network/DNS/timeout)."""


class YoloResponseError(YoloClientError):
    """Raised when the YOLO service responded, but with an error status or bad payload."""

    def __init__(self, message: str, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "retrying YOLO service call (attempt %d/%d) after error: %s",
        retry_state.attempt_number,
        settings.yolo_service_max_retries,
        exc,
    )


# Retryable: connection failures and timeouts. Retryable 5xx status codes are
# raised as `httpx.HTTPStatusError` by `raise_for_status()` and are also
# covered here; 4xx errors (client errors — bad input) are deliberately NOT
# retried, since retrying a malformed request just wastes time.
_RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def _yolo_retry(func):
    return retry(
        reraise=True,
        stop=stop_after_attempt(settings.yolo_service_max_retries),
        wait=wait_exponential(
            multiplier=settings.yolo_service_retry_backoff_seconds,
            max=settings.yolo_service_retry_max_backoff_seconds,
        ),
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
        before_sleep=_log_retry,
    )(func)


class YoloClient:
    """Thin, resilient async HTTP client for citymind-yolo-service.

    Intended to be constructed once (e.g. at application startup) and reused
    across requests via dependency injection, so that the underlying
    connection pool is shared. Use as an async context manager, or call
    `aclose()` explicitly during application shutdown.
    """

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        connect_timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.yolo_service_base_url).rstrip("/")
        timeout = httpx.Timeout(
            timeout=timeout_seconds or settings.yolo_service_timeout_seconds,
            connect=connect_timeout_seconds or settings.yolo_service_connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def __aenter__(self) -> "YoloClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- internal request plumbing -------------------------------------------------

    async def _send(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        url = f"{self.base_url}{path}"

        @_yolo_retry
        async def _do_request() -> httpx.Response:
            logger.info("calling YOLO service: %s %s", method, url)
            response = await self._client.request(method, path, **kwargs)
            response.raise_for_status()
            return response

        try:
            response = await _do_request()
        except _RETRYABLE_EXCEPTIONS as exc:
            logger.error("YOLO service unreachable at %s after retries: %s", url, exc)
            raise YoloConnectionError(f"could not reach YOLO service at {url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            logger.error(
                "YOLO service returned an error: %s %s -> %s",
                method,
                url,
                exc.response.status_code,
            )
            detail: Any
            try:
                detail = exc.response.json()
            except ValueError:
                detail = exc.response.text
            raise YoloResponseError(
                f"YOLO service returned HTTP {exc.response.status_code} for {method} {path}",
                status_code=exc.response.status_code,
                detail=detail,
            ) from exc
        except httpx.HTTPError as exc:
            logger.error("unexpected HTTP error calling YOLO service %s %s: %s", method, url, exc)
            raise YoloConnectionError(f"unexpected error calling YOLO service at {url}: {exc}") from exc

        try:
            return response.json()
        except ValueError as exc:
            logger.error("YOLO service returned a non-JSON body for %s %s", method, url)
            raise YoloResponseError(f"YOLO service returned a non-JSON response for {method} {path}") from exc

    # -- public API -------------------------------------------------------------

    async def health(self) -> dict[str, Any]:
        """GET /health — check whether the YOLO service and its models are up."""
        return await self._send("GET", "/health")

    async def detect_image(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        camera_id: str | None = None,
    ) -> dict[str, Any]:
        """POST /detect/image — upload a single image, get back one DetectionEvent."""
        params: dict[str, Any] = {}
        if camera_id:
            params["camera_id"] = camera_id

        files = {"file": (filename, file_bytes, content_type)}
        return await self._send("POST", "/detect/image", params=params, files=files)

    async def detect_video(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str = "application/octet-stream",
        camera_id: str | None = None,
        max_frames: int | None = None,
    ) -> dict[str, Any]:
        """POST /detect/video — upload a video file, get back many DetectionEvents."""
        params: dict[str, Any] = {}
        if camera_id:
            params["camera_id"] = camera_id
        if max_frames is not None:
            params["max_frames"] = max_frames

        files = {"file": (filename, file_bytes, content_type)}
        return await self._send("POST", "/detect/video", params=params, files=files)

    async def detect_stream(
        self,
        source_url: str,
        camera_id: str | None = None,
        max_frames: int | None = None,
    ) -> dict[str, Any]:
        """POST /detect/stream — connect to a live stream (RTSP/HTTP) the YOLO service can open."""
        payload: dict[str, Any] = {"source_url": source_url}
        if camera_id:
            payload["camera_id"] = camera_id
        if max_frames is not None:
            payload["max_frames"] = max_frames

        return await self._send("POST", "/detect/stream", json=payload)

    async def detect_webcam(
        self,
        device_index: int = 0,
        camera_id: str | None = None,
        max_frames: int = 30,
    ) -> dict[str, Any]:
        """POST /detect/webcam — read from a local camera device index on the YOLO service host."""
        payload: dict[str, Any] = {"device_index": device_index, "max_frames": max_frames}
        if camera_id:
            payload["camera_id"] = camera_id

        return await self._send("POST", "/detect/webcam", json=payload)
