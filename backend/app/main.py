"""
citymind-backend — FastAPI entrypoint.

This service is the Event Aggregator layer of CityMind (a client of
citymind-yolo-service that turns frame-level detections into incident-level
events) *and* the City Logic layer (routing, road-closure simulation,
facility-location recommendation via `app.core.city_logic`), backed by
PostgreSQL persistence and dashboard aggregation APIs. It also hosts the
real-time push layer (`/ws/live`) and an optional continuous camera-polling
pipeline (`app.services.live_monitor`).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.ai_routes import router as ai_router
from app.api.city_routes import router as city_router
from app.api.copilot_routes import router as copilot_router
from app.api.dashboard_routes import router as dashboard_router
from app.api.heatmap_routes import router as heatmap_router
from app.api.routes import router as core_router
from app.api.scenario_routes import router as scenario_router
from app.api.ws_routes import router as ws_router
from app.config import settings
from app.db.session import check_connection, create_all_tables, dispose_engine
from app.services.city_service import CityService
from app.services.event_aggregator import EventAggregator
from app.services.live_monitor import LiveCameraSource, LiveMonitor
from app.services.realtime import RealtimeHub
from app.services.yolo_client import YoloClient

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "starting %s, upstream YOLO service at %s",
        settings.service_name,
        settings.yolo_service_base_url,
    )

    # One shared, connection-pooled YoloClient for the whole app lifetime.
    app.state.yolo_client = YoloClient(base_url=settings.yolo_service_base_url)

    # One shared EventAggregator so incidents can span multiple requests
    # (e.g. successive video clips or polling intervals from the same
    # camera), guarded internally by an asyncio.Lock.
    app.state.event_aggregator = EventAggregator()

    # Real-time push layer: WebSocket fan-out, optionally backed by Redis
    # Pub/Sub for multi-process/multi-replica deployments.
    app.state.realtime_hub = RealtimeHub(redis_url=settings.redis_url)
    await app.state.realtime_hub.start()

    # Database: verify connectivity (non-fatal) and, in dev, optionally
    # create tables directly from ORM metadata as a fallback for when
    # Alembic migrations haven't been run yet.
    db_ok = await check_connection()
    if db_ok:
        logger.info("database connection OK")
        if settings.database_auto_create_tables:
            logger.info("database_auto_create_tables=True — creating tables from ORM metadata")
            await create_all_tables()
    else:
        logger.warning(
            "database is not reachable at startup — persistence and /dashboard/* endpoints "
            "will fail until connectivity is restored"
        )

    # City Logic: build the road network graph + population data once, in a
    # worker thread, and keep it in memory for the process lifetime. This is
    # a heavyweight, network- and disk-dependent pipeline, so failures are
    # logged and leave city_service in a not-ready state rather than
    # crashing application startup.
    app.state.city_service = CityService(
        area_query=settings.city_logic_area_query,
        radius_m=settings.city_logic_radius_m,
        tif_path=settings.city_logic_worldpop_tif_path,
        kdtree_radius_deg=settings.city_logic_kdtree_radius_deg,
        congestion_multiplier=settings.city_logic_congestion_multiplier,
        default_n_candidates=settings.city_logic_n_facility_candidates,
    )
    if settings.city_logic_enabled:
        await app.state.city_service.initialize()
        if not app.state.city_service.ready:
            logger.warning(
                "city_logic did not initialize (%s) — /city/* endpoints will return 503 until it does",
                app.state.city_service.init_error,
            )
    else:
        logger.info("city_logic_enabled=False — skipping city_logic initialization, /city/* will return 503")

    # Optional continuous ML -> backend pipeline: polls real camera/stream
    # URLs configured via LIVE_CAMERA_SOURCES. No-op (starts zero tasks) when
    # unconfigured, which is the default.
    live_sources = [
        LiveCameraSource(
            camera_id=source["camera_id"],
            source_url=source["source_url"],
            poll_interval_seconds=float(source.get("poll_interval_seconds", 10.0)),
            max_frames=source.get("max_frames"),
        )
        for source in settings.parsed_live_camera_sources
        if "camera_id" in source and "source_url" in source
    ]
    app.state.live_monitor = LiveMonitor(
        yolo_client=app.state.yolo_client,
        aggregator=app.state.event_aggregator,
        realtime_hub=app.state.realtime_hub,
        sources=live_sources,
    )
    app.state.live_monitor.start()

    try:
        yield
    finally:
        logger.info("shutting down %s", settings.service_name)
        await app.state.live_monitor.stop()
        await app.state.yolo_client.aclose()
        await app.state.realtime_hub.stop()
        await dispose_engine()


app = FastAPI(
    title=settings.service_name,
    description=(
        "CityMind backend: Event Aggregator (YOLO detection -> incidents), City Logic "
        "(routing / closure simulation / facility recommendation), real-time WebSocket push, "
        "and dashboard APIs."
    ),
    version="1.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(core_router)
app.include_router(city_router)
app.include_router(dashboard_router)
app.include_router(copilot_router)
app.include_router(ai_router)
app.include_router(heatmap_router)
app.include_router(scenario_router)
app.include_router(ws_router)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request, exc: StarletteHTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": exc.errors()})
