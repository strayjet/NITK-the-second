"""
CityService — owns the loaded road-network graph + population data and
exposes async wrappers around `app.core.city_logic`.

`city_logic.initialize()` is a heavyweight, blocking, I/O- and CPU-bound
pipeline (OSMnx graph download + raster processing). It is run exactly once,
in a worker thread via `asyncio.to_thread`, during application startup
(see `app.main.lifespan`), and the resulting graph/population data are kept
in memory for the lifetime of the process — never rebuilt per-request.

If initialization fails (missing raster file, no network access, bad area
query, etc.) the service is left in a `ready=False` state instead of
crashing the whole application: `/city/*` endpoints then respond with a
clear 503 instead of the process refusing to start at all.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import networkx as nx

from app.core import city_logic

logger = logging.getLogger(__name__)


class CityLogicNotReadyError(RuntimeError):
    """Raised when a `/city/*` request arrives before (or after a failed) initialization."""


class CityLogicComputationError(RuntimeError):
    """Raised when a city_logic computation fails for a reason unrelated to readiness
    (e.g. a coordinate that doesn't resolve to a graph node)."""


class CityService:
    """Holds the loaded graph/population state and serializes access to city_logic."""

    def __init__(
        self,
        *,
        area_query: str,
        radius_m: int,
        tif_path: str,
        kdtree_radius_deg: float,
        congestion_multiplier: float,
        default_n_candidates: int,
    ) -> None:
        self._area_query = area_query
        self._radius_m = radius_m
        self._tif_path = tif_path
        self._kdtree_radius_deg = kdtree_radius_deg
        self._congestion_multiplier = congestion_multiplier
        self._default_n_candidates = default_n_candidates

        self.graph: nx.MultiDiGraph | None = None
        self.node_population = None
        self.ready: bool = False
        self.init_error: str | None = None
        # Serializes writes to `self.graph` (e.g. re-initialization); reads
        # are safe to run concurrently since city_logic never mutates G in
        # place for routing (simulate_closure_impact copies before edits).
        self._lock = asyncio.Lock()

        # -- Persistent "Block Road" state ---------------------------------
        # Roads blocked via /city/block-road stay blocked - for every route
        # computed by every user - until explicitly unblocked or cleared.
        # This is process-lifetime, in-memory state (same durability as
        # `self.graph` itself, which is also never persisted to disk), kept
        # separate from `self.graph` so the master graph is never mutated:
        # `self._routing_graph` is a derived copy with blocked edges
        # removed, rebuilt only when the blocked set changes.
        self._blocked_edges: set[tuple[int, int]] = set()
        self._routing_graph: nx.MultiDiGraph | None = None
        self._closures_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Run the full city_logic pipeline once, off the event loop."""
        async with self._lock:
            logger.info("initializing city_logic for area=%r radius_m=%s", self._area_query, self._radius_m)
            try:
                graph, node_population = await asyncio.to_thread(
                    city_logic.initialize,
                    area_query=self._area_query,
                    radius_m=self._radius_m,
                    tif_path=self._tif_path,
                    kdtree_radius_deg=self._kdtree_radius_deg,
                    congestion_multiplier=self._congestion_multiplier,
                )
            except Exception as exc:  # noqa: BLE001 - must not crash app startup
                logger.exception("city_logic initialization failed")
                self.ready = False
                self.init_error = str(exc)
                return

            self.graph = graph
            self.node_population = node_population
            self.ready = True
            self.init_error = None
            self._routing_graph = graph  # no closures yet - routing graph == master graph
            logger.info(
                "city_logic ready: %d nodes, %d edges",
                self.graph.number_of_nodes(),
                self.graph.number_of_edges(),
            )

    def _ensure_ready(self) -> None:
        if not self.ready or self.graph is None or self.node_population is None:
            raise CityLogicNotReadyError(
                self.init_error or "city routing engine is not initialized yet"
            )

    async def get_route(self, origin: tuple[float, float], dest: tuple[float, float]) -> dict[str, Any]:
        self._ensure_ready()
        # Blocked roads (see block_road/unblock_road below) are removed from
        # every routing calculation - route_nodes_to_coords below still
        # needs the same graph the route was computed on, so both use
        # `graph` here rather than `self.graph`.
        graph = self._routing_graph

        def _work():
            try:
                route, length_m = city_logic.get_route(graph, origin, dest)
            except nx.NetworkXNoPath as exc:
                raise CityLogicComputationError("no route exists between the given points") from exc
            except nx.NodeNotFound as exc:
                raise CityLogicComputationError(f"coordinate could not be matched to the road network: {exc}") from exc
            # route_nodes_to_coords follows real road curvature (edge geometry)
            # instead of cutting straight lines across intersections.
            coords = city_logic.route_nodes_to_coords(graph, route)
            return route, length_m, coords

        return await self._run(_work)

    async def simulate_closure(
        self,
        origin: tuple[float, float],
        dest: tuple[float, float],
        edge: tuple[int, int],
    ) -> dict[str, Any]:
        self._ensure_ready()
        graph = self.graph

        def _work():
            if not graph.has_edge(*edge):
                raise CityLogicComputationError(f"edge {edge} does not exist in the road network")
            try:
                return city_logic.simulate_closure_impact(graph, origin, dest, edge)
            except nx.NodeNotFound as exc:
                raise CityLogicComputationError(f"coordinate could not be matched to the road network: {exc}") from exc

        return await self._run(_work)

    async def recommend_facility(
        self,
        n_candidates: int | None = None,
        facility_type: str = "hospital",
    ) -> dict[str, Any]:
        self._ensure_ready()
        graph = self.graph
        population = self.node_population
        candidates = n_candidates or self._default_n_candidates

        def _work():
            return city_logic.recommend_facility_location(
                graph, population, facility_type=facility_type, n_candidates=candidates
            )

        return await self._run(_work)

    async def suggest_roads(
        self,
        sample_size: int = 100,
        min_straight_line_m: float = 200,
        max_straight_line_m: float = 2000,
        top_n: int = 10,
    ) -> list[dict[str, Any]]:
        """Finds and scores road-gap candidates (missing direct connections)."""
        self._ensure_ready()
        graph = self.graph
        population = self.node_population

        def _work():
            candidates = city_logic.find_road_gap_candidates(
                graph,
                node_population=population,
                sample_size=sample_size,
                min_straight_line_m=min_straight_line_m,
                max_straight_line_m=max_straight_line_m,
            )
            return city_logic.score_candidates(graph, candidates, population, top_n=top_n)

        return await self._run(_work)

    async def nearest_edge(self, lat: float, lon: float) -> dict[str, Any]:
        """Snap a clicked map point to the nearest existing road edge (for the road-closure picker)."""
        self._ensure_ready()
        graph = self.graph

        def _work():
            return city_logic.nearest_edge(graph, lat, lon)

        return await self._run(_work)

    async def get_edges(self) -> list[dict[str, Any]]:
        """Full road network (every edge, curved geometry included) for the
        Road Closure Simulation map mode's vector layer. Each edge is
        annotated with `blocked: bool` so the whole-network view can render
        currently-blocked roads in red without a second request."""
        self._ensure_ready()
        graph = self.graph
        blocked = self._blocked_edges

        def _work():
            edges = city_logic.get_all_edges(graph)
            for edge in edges:
                edge["blocked"] = (edge["node_u"], edge["node_v"]) in blocked or (
                    edge["node_v"],
                    edge["node_u"],
                ) in blocked
            return edges

        return await self._run(_work)

    # -- Persistent "Block Road" mode ---------------------------------------

    def _rebuild_routing_graph(self) -> None:
        """Recomputes the closures-aware routing graph from the untouched
        master graph. Must be called (while holding `_closures_lock`)
        whenever `self._blocked_edges` changes."""
        self._routing_graph = city_logic.remove_blocked_edges(self.graph, self._blocked_edges)

    async def _blocked_roads_payload(self) -> list[dict[str, Any]]:
        """Every currently-blocked road, with geometry, for the frontend's
        persistent red "blocked roads" map layer."""
        graph = self.graph
        blocked = sorted(self._blocked_edges)

        def _work():
            items = []
            for u, v in blocked:
                try:
                    geometry = city_logic.get_edge_coords(graph, u, v)
                except Exception:  # noqa: BLE001 - edge direction may only exist as (v, u)
                    try:
                        geometry = city_logic.get_edge_coords(graph, v, u)
                    except Exception:  # noqa: BLE001
                        geometry = []
                items.append({"node_u": u, "node_v": v, "geometry": geometry})
            return items

        return await self._run(_work)

    async def block_road(self, edge: tuple[int, int]) -> list[dict[str, Any]]:
        """Marks `edge` (a (u, v) node-id pair) as closed. The edge is
        removed from routing calculations (both travel directions) and
        stays blocked - across every subsequent route computed by every
        user - until unblocked or cleared. Every other part of the graph
        (nodes, other edges, population/weight data) is left untouched."""
        self._ensure_ready()
        u, v = edge
        if not (self.graph.has_edge(u, v) or self.graph.has_edge(v, u)):
            raise CityLogicComputationError(f"edge {edge} does not exist in the road network")

        async with self._closures_lock:
            self._blocked_edges.add((u, v))
            self._rebuild_routing_graph()

        return await self._blocked_roads_payload()

    async def unblock_road(self, edge: tuple[int, int]) -> list[dict[str, Any]]:
        """Reopens a previously-blocked road. A no-op (not an error) if the
        edge wasn't blocked - unblocking is idempotent."""
        self._ensure_ready()
        u, v = edge

        async with self._closures_lock:
            self._blocked_edges.discard((u, v))
            self._blocked_edges.discard((v, u))
            self._rebuild_routing_graph()

        return await self._blocked_roads_payload()

    async def clear_closures(self) -> list[dict[str, Any]]:
        """Reopens every currently-blocked road at once."""
        self._ensure_ready()
        async with self._closures_lock:
            self._blocked_edges.clear()
            self._rebuild_routing_graph()

        return []

    async def list_closed_roads(self) -> list[dict[str, Any]]:
        """Every currently-blocked road, with geometry (see
        `_blocked_roads_payload`). Read-only - safe to call before the
        service is otherwise mutated."""
        self._ensure_ready()
        return await self._blocked_roads_payload()

    def get_facility_types(self) -> list[dict[str, Any]]:
        """Passthrough to the facility catalog — doesn't need the graph to be ready."""
        return city_logic.get_all_facility_types()

    @staticmethod
    def route_to_coords_safe(graph: nx.MultiDiGraph | None, route: list[int] | None) -> list[list[float]] | None:
        """`city_logic.route_nodes_to_coords`, tolerant of a missing graph/route (e.g. not-ready state)."""
        if graph is None or route is None:
            return None
        return city_logic.route_nodes_to_coords(graph, route)

    @staticmethod
    async def _run(fn):
        try:
            return await asyncio.to_thread(fn)
        except CityLogicComputationError:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the API layer as a 422/500
            logger.exception("city_logic computation failed")
            raise CityLogicComputationError(str(exc)) from exc
