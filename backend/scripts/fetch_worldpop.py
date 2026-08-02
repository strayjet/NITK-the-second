#!/usr/bin/env python3
"""
fetch_worldpop.py — ensures a valid WorldPop population raster exists at
`CITY_LOGIC_WORLDPOP_TIF_PATH` before the backend starts, so a fresh
`docker compose up --build` never requires the operator to manually copy a
.tif file onto the host.

Why this exists
----------------
`app.core.city_logic.initialize()` needs a real single-band GeoTIFF at
startup. Historically this repo shipped a docker-compose bind mount
(`./data/worldpop.tif:/app/worldpop.tif`) pointing at a host path that was
never created. Docker's bind-mount semantics silently create an *empty
directory* at that path rather than failing the `docker compose up` command,
so the backend received a directory where rasterio expected a file — the
"not recognized as being in a supported file format" error.

This script closes that gap:
  1. If a valid raster already sits at the target path (e.g. the operator
     mounted their own, or a previous run already downloaded one into the
     persistent `citymind_geodata` volume), do nothing.
  2. If the target path is missing, empty, a directory, or not a readable
     single-band raster, (re)download it from a public, redistributable
     WorldPop source (CC BY 4.0 — see https://www.worldpop.org/data/licence.txt)
     into a temp file, validate it, and atomically move it into place.
  3. On any failure (no network, DNS blocked, disk full, corrupt download),
     log a clear warning and exit 0 — never fail the container's startup
     command. `CityService.initialize()` already handles a missing/invalid
     raster gracefully (city_logic stays in a `ready=False` state and
     `/city/*` returns 503 with a clear message instead of the process
     crashing), so a failed fetch here degrades the same way it always did,
     it just no longer does so because of a Docker footgun.

Configuration (all optional, sane defaults applied):
  CITY_LOGIC_WORLDPOP_TIF_PATH       target file path (default: /app/data/worldpop.tif)
  CITY_LOGIC_WORLDPOP_DOWNLOAD_URL   source URL (default: WorldPop India 2020, 1km, matching
                                      the default CITY_LOGIC_AREA_QUERY of "Mumbai, Maharashtra, India")
  CITY_LOGIC_ENABLED                 if "false", this script is a no-op (city_logic is disabled anyway)
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request

DEFAULT_TARGET = "/app/data/worldpop.tif"
# WorldPop "Unconstrained individual countries 2000-2020, 1km resolution" —
# India, 2020. Public, freely redistributable under CC BY 4.0.
# https://hub.worldpop.org/geodata/summary?id=... / https://www.worldpop.org/data/licence.txt
DEFAULT_SOURCE = (
    "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/2020/IND/"
    "ind_ppp_2020_1km_Aggregated.tif"
)
MIN_VALID_BYTES = 4096  # a real 1km-resolution country raster is at least several hundred KB
CONNECT_TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 3


def _log(message: str) -> None:
    print(f"[fetch_worldpop] {message}", flush=True)


def _is_valid_raster(path: str) -> bool:
    """Returns True only if `path` is a file (not a directory) that rasterio
    can actually open as a raster with at least one band."""
    if not os.path.isfile(path):
        return False
    if os.path.getsize(path) < MIN_VALID_BYTES:
        return False
    try:
        import rasterio  # imported lazily so this script never fails on

        # environments where the backend's venv isn't active for some reason.
        with rasterio.open(path) as src:
            return src.count >= 1
    except Exception as exc:  # noqa: BLE001 - any failure means "not valid"
        _log(f"existing file at {path!r} failed raster validation: {exc}")
        return False


def _remove_existing(path: str) -> None:
    """Removes whatever is at `path`, whether it's a stale file, an empty
    Docker-created directory (the historical bug), or a partial download."""
    if os.path.isdir(path):
        _log(f"removing empty/invalid directory at {path} (Docker bind-mount artifact)")
        shutil.rmtree(path, ignore_errors=True)
    elif os.path.exists(path):
        _log(f"removing invalid/partial file at {path}")
        os.remove(path)


def _download(url: str, dest_tmp: str) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "citymind-backend/1.0"})
    with urllib.request.urlopen(request, timeout=CONNECT_TIMEOUT_SECONDS) as response, open(
        dest_tmp, "wb"
    ) as out_file:
        shutil.copyfileobj(response, out_file, length=1024 * 1024)


def main() -> int:
    if os.environ.get("CITY_LOGIC_ENABLED", "true").strip().lower() in {"false", "0", "no"}:
        _log("CITY_LOGIC_ENABLED=false — skipping WorldPop fetch")
        return 0

    target = os.environ.get("CITY_LOGIC_WORLDPOP_TIF_PATH", DEFAULT_TARGET)
    source_url = os.environ.get("CITY_LOGIC_WORLDPOP_DOWNLOAD_URL", DEFAULT_SOURCE)

    if _is_valid_raster(target):
        _log(f"valid raster already present at {target} — nothing to do")
        return 0

    _remove_existing(target)
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)

    _log(f"downloading WorldPop raster from {source_url}")
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        tmp_path = target + ".partial"
        try:
            _download(source_url, tmp_path)
            if not _is_valid_raster(tmp_path):
                raise ValueError("downloaded file failed raster validation")
            os.replace(tmp_path, target)  # atomic on the same filesystem
            _log(f"download OK — valid raster saved to {target}")
            return 0
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            last_error = exc
            _log(f"attempt {attempt}/{MAX_ATTEMPTS} failed: {exc}")
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            if attempt < MAX_ATTEMPTS:
                time.sleep(RETRY_BACKOFF_SECONDS * attempt)

    _log(
        f"could not obtain a valid WorldPop raster after {MAX_ATTEMPTS} attempts "
        f"({last_error}). Continuing startup anyway — city_logic will report "
        f"ready=False and /city/* will return 503 until a valid file exists at "
        f"{target} (mount your own, or retry with network access)."
    )
    return 0  # never fail container startup because of this


if __name__ == "__main__":
    sys.exit(main())
