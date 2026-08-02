"""
HTTP routes for the City Logic layer (routing, closure simulation, facility
location recommendation), backed by `app.services.city_service.CityService`.

Every endpoint here persists its result to PostgreSQL (`route_analyses` /
`facilities`) via the repository layer, so `/dashboard/*` can report on
historical routing and facility-planning activity.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.repositories.facility_repository import FacilityRepository
from app.repositories.route_repository import RouteAnalysisRepository
from app.schemas.city import (
    BlockedRoadItem,
    BlockRoadRequest,
    ClosedRoadsResponse,
    ClosureSimulationRequest,
    ClosureSimulationResponse,
    FacilityRecommendationRequest,
    FacilityRecommendationResponse,
    FacilityTypeInfo,
    FacilityTypesResponse,
    NearestEdgeRequest,
    NearestEdgeResponse,
    RoadEdgeItem,
    RoadNetworkResponse,
    RoadSuggestionItem,
    RoadSuggestionRequest,
    RoadSuggestionResponse,
    RouteRequest,
    RouteResponse,
    UnblockRoadRequest,
)
from app.services.city_service import CityLogicComputationError, CityLogicNotReadyError, CityService
from app.services.realtime import RealtimeHub
from app.utils.cache import dashboard_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/city", tags=["city"])


def get_city_service(request: Request) -> CityService:
    return request.app.state.city_service


def get_realtime_hub(request: Request) -> RealtimeHub:
    return request.app.state.realtime_hub


def _raise_for_city_error(exc: Exception) -> None:
    if isinstance(exc, CityLogicNotReadyError):
        raise HTTPException(status_code=503, detail=f"city routing engine unavailable: {exc}") from exc
    if isinstance(exc, CityLogicComputationError):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.exception("unexpected error in city routes")
    raise HTTPException(status_code=500, detail="internal error computing city logic result") from exc


@router.post("/route", response_model=RouteResponse)
async def compute_route(
    payload: RouteRequest,
    city_service: CityService = Depends(get_city_service),
    db: AsyncSession = Depends(get_db),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> RouteResponse:
    """Shortest population-aware route between two coordinates, with real physical distance."""
    try:
        route, length_m, coords = await city_service.get_route(payload.origin, payload.destination)
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    distance_km = length_m / 1000.0

    repo = RouteAnalysisRepository(db)
    await repo.create(
        analysis_type="ROUTE",
        status="OK",
        origin_lat=payload.origin[0],
        origin_lon=payload.origin[1],
        dest_lat=payload.destination[0],
        dest_lon=payload.destination[1],
        distance_km=distance_km,
        route_node_count=len(route),
        route_coordinates=coords,
    )
    await dashboard_cache.invalidate()
    await realtime_hub.broadcast("dashboard.invalidated", {"reason": "route_computed"})

    return RouteResponse(distance_km=distance_km, route_coordinates=coords)


@router.post("/simulate-closure", response_model=ClosureSimulationResponse)
async def simulate_closure(
    payload: ClosureSimulationRequest,
    city_service: CityService = Depends(get_city_service),
    db: AsyncSession = Depends(get_db),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> ClosureSimulationResponse:
    """Compare routes before/after closing a specific road edge."""
    try:
        result = await city_service.simulate_closure(
            payload.origin, payload.destination, payload.edge_to_close
        )
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    repo = RouteAnalysisRepository(db)

    if "status" in result and "route_before" not in result:
        # No alternative route exists after closure.
        await repo.create(
            analysis_type="CLOSURE",
            status="NO_ALTERNATIVE",
            origin_lat=payload.origin[0],
            origin_lon=payload.origin[1],
            dest_lat=payload.destination[0],
            dest_lon=payload.destination[1],
            closed_edge_u=payload.edge_to_close[0],
            closed_edge_v=payload.edge_to_close[1],
        )
        await dashboard_cache.invalidate()
        await realtime_hub.broadcast("dashboard.invalidated", {"reason": "closure_no_alternative"})
        return ClosureSimulationResponse(status=result["status"])

    graph = city_service.graph
    route_before_coords = city_service.route_to_coords_safe(graph, result["route_before"])
    route_after_coords = city_service.route_to_coords_safe(graph, result["route_after"])

    await repo.create(
        analysis_type="CLOSURE",
        status="OK",
        origin_lat=payload.origin[0],
        origin_lon=payload.origin[1],
        dest_lat=payload.destination[0],
        dest_lon=payload.destination[1],
        closed_edge_u=payload.edge_to_close[0],
        closed_edge_v=payload.edge_to_close[1],
        length_before_km=result["length_before_km"],
        length_after_km=result["length_after_km"],
        delay_km=result["delay_km"],
        route_coordinates=route_after_coords,
    )
    await dashboard_cache.invalidate()
    await realtime_hub.broadcast("dashboard.invalidated", {"reason": "closure_simulated"})

    return ClosureSimulationResponse(
        status="OK",
        route_before=result["route_before"],
        length_before_km=result["length_before_km"],
        route_after=result["route_after"],
        length_after_km=result["length_after_km"],
        delay_km=result["delay_km"],
        route_before_coordinates=route_before_coords,
        route_after_coordinates=route_after_coords,
    )


@router.post("/nearest-edge", response_model=NearestEdgeResponse)
async def nearest_edge(
    payload: NearestEdgeRequest,
    city_service: CityService = Depends(get_city_service),
) -> NearestEdgeResponse:
    """
    Snap a clicked map point to the nearest existing road edge.

    Read-only lookup (no persistence, no cache invalidation) that powers the
    map-first road-closure picker: the frontend sends a click's lat/lon and
    gets back the real (u, v) OSM edge plus its curved geometry to highlight,
    instead of requiring the user to type raw OSM node ids.
    """
    try:
        result = await city_service.nearest_edge(payload.lat, payload.lon)
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    return NearestEdgeResponse(**result)


@router.get("/edges", response_model=RoadNetworkResponse)
async def list_edges(
    city_service: CityService = Depends(get_city_service),
) -> RoadNetworkResponse:
    """
    Full road network — every edge with curved geometry, endpoints, and
    length — for the "Road Closure Simulation" map mode, where the entire
    network is rendered as a hoverable/clickable vector layer instead of
    resolving one edge per click via /city/nearest-edge.

    Read-only lookup (no persistence, no cache invalidation), same as
    /city/nearest-edge.
    """
    try:
        edges = await city_service.get_edges()
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    return RoadNetworkResponse(edges=[RoadEdgeItem(**edge) for edge in edges])


@router.post("/block-road", response_model=ClosedRoadsResponse)
async def block_road(
    payload: BlockRoadRequest,
    city_service: CityService = Depends(get_city_service),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> ClosedRoadsResponse:
    """
    Blocks a road (edge) so it is excluded from every routing calculation -
    for every user - until it is unblocked or all closures are cleared.
    Multiple roads may be blocked at once; every other part of the road
    network is left untouched. Returns the full updated set of blocked
    roads (with geometry) so the frontend can redraw its red "closed
    roads" layer directly from the response.
    """
    try:
        blocked = await city_service.block_road(payload.edge)
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    await dashboard_cache.invalidate()
    await realtime_hub.broadcast("dashboard.invalidated", {"reason": "road_blocked"})
    return ClosedRoadsResponse(blocked_edges=[BlockedRoadItem(**item) for item in blocked])


@router.post("/unblock-road", response_model=ClosedRoadsResponse)
async def unblock_road(
    payload: UnblockRoadRequest,
    city_service: CityService = Depends(get_city_service),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> ClosedRoadsResponse:
    """Reopens a previously-blocked road. Idempotent - unblocking a road
    that isn't currently blocked is not an error."""
    try:
        blocked = await city_service.unblock_road(payload.edge)
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    await dashboard_cache.invalidate()
    await realtime_hub.broadcast("dashboard.invalidated", {"reason": "road_unblocked"})
    return ClosedRoadsResponse(blocked_edges=[BlockedRoadItem(**item) for item in blocked])


@router.post("/clear-closures", response_model=ClosedRoadsResponse)
async def clear_closures(
    city_service: CityService = Depends(get_city_service),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> ClosedRoadsResponse:
    """Reopens every currently-blocked road at once."""
    try:
        blocked = await city_service.clear_closures()
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    await dashboard_cache.invalidate()
    await realtime_hub.broadcast("dashboard.invalidated", {"reason": "closures_cleared"})
    return ClosedRoadsResponse(blocked_edges=[BlockedRoadItem(**item) for item in blocked])


@router.get("/closed-roads", response_model=ClosedRoadsResponse)
async def closed_roads(
    city_service: CityService = Depends(get_city_service),
) -> ClosedRoadsResponse:
    """Every currently-blocked road, with geometry - read-only, no
    persistence/cache invalidation, safe to poll on page load."""
    try:
        blocked = await city_service.list_closed_roads()
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    return ClosedRoadsResponse(blocked_edges=[BlockedRoadItem(**item) for item in blocked])


@router.post("/recommend-facility", response_model=FacilityRecommendationResponse)
async def recommend_facility(
    payload: FacilityRecommendationRequest,
    city_service: CityService = Depends(get_city_service),
    db: AsyncSession = Depends(get_db),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> FacilityRecommendationResponse:
    """Recommend a facility site that minimizes population-weighted access cost."""
    n_candidates = payload.n_candidates or settings.city_logic_n_facility_candidates
    if n_candidates > settings.city_logic_max_facility_candidates:
        raise HTTPException(
            status_code=422,
            detail=f"n_candidates must be <= {settings.city_logic_max_facility_candidates}",
        )

    try:
        result = await city_service.recommend_facility(
            n_candidates=n_candidates, facility_type=payload.facility_type
        )
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    if result.get("status") == "no_valid_site":
        # No candidate satisfies the spacing constraint against existing
        # same-type facilities — not an error, just nothing to persist.
        return FacilityRecommendationResponse(
            status="no_valid_site",
            detail=result.get("detail"),
            n_candidates=n_candidates,
            facility_type=payload.facility_type,
        )

    if result["cost"] == float("inf"):
        raise HTTPException(
            status_code=422,
            detail="no viable facility candidate could reach enough of the demand population",
        )

    facility_id = None
    if payload.persist:
        repo = FacilityRepository(db)
        facility = await repo.create(
            node_id=str(result["node_id"]),
            lat=result["lat"],
            lon=result["lon"],
            cost=result["cost"],
            n_candidates=n_candidates,
            candidate_costs={str(k): (v if v != float("inf") else None) for k, v in result["all_costs"].items()},
            facility_type=payload.facility_type,
            name=payload.name,
            min_spacing_km=result.get("min_spacing_km"),
            existing_facilities_considered=result.get("existing_facilities_considered"),
        )
        facility_id = facility.id
        await dashboard_cache.invalidate()
        await realtime_hub.broadcast("dashboard.invalidated", {"reason": "facility_recommended"})

    return FacilityRecommendationResponse(
        facility_id=facility_id,
        node_id=str(result["node_id"]),
        lat=result["lat"],
        lon=result["lon"],
        cost=result["cost"],
        n_candidates=n_candidates,
        facility_type=result.get("facility_type", payload.facility_type),
        label=result.get("label"),
        color=result.get("color"),
        min_spacing_km=result.get("min_spacing_km"),
        existing_facilities_considered=result.get("existing_facilities_considered"),
        population_served=result.get("population_served"),
        coverage_improvement_pct=result.get("coverage_improvement_pct"),
        confidence_score=result.get("confidence_score"),
        nearby_facilities=result.get("nearby_facilities"),
    )


@router.get("/facility-types", response_model=FacilityTypesResponse)
async def facility_types(
    city_service: CityService = Depends(get_city_service),
) -> FacilityTypesResponse:
    """Full facility catalog (value, label, color, min_spacing_km) for frontend dropdown population."""
    return FacilityTypesResponse(
        facility_types=[FacilityTypeInfo(**entry) for entry in city_service.get_facility_types()]
    )


@router.post("/suggest-roads", response_model=RoadSuggestionResponse)
async def suggest_roads(
    payload: RoadSuggestionRequest,
    city_service: CityService = Depends(get_city_service),
    db: AsyncSession = Depends(get_db),
    realtime_hub: RealtimeHub = Depends(get_realtime_hub),
) -> RoadSuggestionResponse:
    """Suggest missing direct road connections (network augmentation), scored by population-weighted impact."""
    try:
        scored = await city_service.suggest_roads(
            sample_size=payload.sample_size,
            min_straight_line_m=payload.min_straight_line_m,
            max_straight_line_m=payload.max_straight_line_m,
            top_n=payload.top_n,
        )
    except (CityLogicNotReadyError, CityLogicComputationError) as exc:
        _raise_for_city_error(exc)
    except Exception as exc:  # noqa: BLE001
        _raise_for_city_error(exc)

    graph = city_service.graph
    suggestions = [
        RoadSuggestionItem(
            node_u=item["node_u"],
            node_v=item["node_v"],
            point_u=[graph.nodes[item["node_u"]]["y"], graph.nodes[item["node_u"]]["x"]],
            point_v=[graph.nodes[item["node_v"]]["y"], graph.nodes[item["node_v"]]["x"]],
            straight_line_m=item["straight_line_m"],
            current_network_dist_m=item["current_network_dist_m"],
            gap_ratio=item["gap_ratio"],
            distance_saved_m=item["distance_saved_m"],
            impact_score=item["impact_score"],
            current_path_coordinates=city_service.route_to_coords_safe(graph, item["current_path"]) or [],
        )
        for item in scored
    ]

    # Road-gap suggestions are exploratory candidates rather than committed
    # infrastructure decisions (unlike facilities), so by default they are
    # not persisted to a dedicated table — see RoadSuggestionRequest.persist.
    # When persist=True we still invalidate the dashboard cache and broadcast
    # so connected clients know a new suggestion run happened, without
    # requiring a dedicated road_suggestions table.
    if payload.persist:
        await dashboard_cache.invalidate()
        await realtime_hub.broadcast("dashboard.invalidated", {"reason": "road_suggestions_computed"})

    return RoadSuggestionResponse(suggestions=suggestions)
