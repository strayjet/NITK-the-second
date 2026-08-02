"""
ScenarioService — "what if" simulation engine.

Supports four scenario types, all computed against the same in-memory road
network + population data owned by `CityService`:

- **road_closure**: close a road edge and compare a specific route's
  distance/ETA before vs. after, plus the citywide coverage impact (using
  the currently-persisted facility set) of losing that edge.
- **facility_add**: add a new facility at a given location and measure the
  citywide population-coverage gain.
- **facility_remove**: remove an existing (persisted) facility and measure
  the citywide population-coverage loss.
- **facility_relocate**: remove one facility and add another in a single
  step, measuring the net coverage change.

"Coverage" is defined as the fraction of total modeled population that can
be reached by *some* facility within `coverage_radius_km` of network
(not straight-line) distance — the same road-network distance model used by
routing and facility recommendation, computed via a multi-source Dijkstra
from every facility node simultaneously.
"""

from __future__ import annotations

import logging
from typing import Any

import networkx as nx
import osmnx as ox

from app.repositories.facility_repository import FacilityRepository
from app.schemas.scenario import CoverageChange, RouteChange, ScenarioRequest, ScenarioResponse
from app.services.city_service import CityLogicComputationError, CityLogicNotReadyError, CityService

logger = logging.getLogger(__name__)

_AVG_SPEED_KMPH = 35.0


class ScenarioService:
    def __init__(self, city_service: CityService, facility_repo: FacilityRepository) -> None:
        self.city_service = city_service
        self.facility_repo = facility_repo

    async def simulate(self, payload: ScenarioRequest) -> ScenarioResponse:
        if not self.city_service.ready or self.city_service.graph is None or self.city_service.node_population is None:
            raise CityLogicNotReadyError(self.city_service.init_error or "city routing engine is not initialized yet")

        graph = self.city_service.graph
        population = self.city_service.node_population
        cutoff_m = payload.coverage_radius_km * 1000.0

        facilities, _ = await self.facility_repo.list_filtered(offset=0, limit=1000)
        baseline_nodes: dict[int, int] = {}  # osm node id -> facility.id (for lookups)
        for f in facilities:
            try:
                baseline_nodes[int(f.node_id)] = f.id
            except (TypeError, ValueError):
                continue

        if payload.scenario_type == "road_closure":
            return await self._simulate_road_closure(graph, population, baseline_nodes, cutoff_m, payload)
        return await self._simulate_facility_change(graph, population, baseline_nodes, cutoff_m, payload)

    # ------------------------------------------------------------------
    # road_closure
    # ------------------------------------------------------------------

    async def _simulate_road_closure(
        self,
        graph: nx.MultiDiGraph,
        population,
        baseline_nodes: dict[int, int],
        cutoff_m: float,
        payload: ScenarioRequest,
    ) -> ScenarioResponse:
        edge = payload.edge_to_close
        if not graph.has_edge(*edge):
            raise CityLogicComputationError(f"edge {edge} does not exist in the road network")

        try:
            result = await self.city_service.simulate_closure(payload.origin, payload.destination, edge)
        except nx.NetworkXNoPath as exc:
            raise CityLogicComputationError("no route exists between the given points") from exc

        route_change: RouteChange
        if "route_before" not in result:
            route_change = RouteChange(status=result.get("status", "no alternative route exists"))
        else:
            before_km = result["length_before_km"]
            after_km = result["length_after_km"]
            eta_before = round((before_km / _AVG_SPEED_KMPH) * 60, 1)
            eta_after = round((after_km / _AVG_SPEED_KMPH) * 60, 1)
            graph_after = self.city_service.route_to_coords_safe(graph, result["route_after"])
            graph_before = self.city_service.route_to_coords_safe(graph, result["route_before"])
            route_change = RouteChange(
                distance_km_before=round(before_km, 3),
                distance_km_after=round(after_km, 3),
                delay_km=round(result["delay_km"], 3),
                eta_minutes_before=eta_before,
                eta_minutes_after=eta_after,
                eta_delay_minutes=round(eta_after - eta_before, 1),
                route_coordinates_before=graph_before,
                route_coordinates_after=graph_after,
                status="OK",
            )

        # Citywide coverage impact of losing this edge, using the currently
        # persisted facility set (unaffected by this scenario type).
        graph_closed = graph.copy()
        graph_closed.remove_edge(*edge)

        facility_nodes = list(baseline_nodes.keys())
        covered_before, total_pop = self._coverage(graph, population, facility_nodes, cutoff_m)
        covered_after, _ = self._coverage(graph_closed, population, facility_nodes, cutoff_m)
        coverage_change = self._build_coverage_change(covered_before, covered_after, total_pop, payload.coverage_radius_km)

        before_metrics = {"distance_km": route_change.distance_km_before, "coverage_fraction": coverage_change.coverage_fraction_before}
        after_metrics = {"distance_km": route_change.distance_km_after, "coverage_fraction": coverage_change.coverage_fraction_after}

        summary = self._summarize_road_closure(route_change, coverage_change)

        return ScenarioResponse(
            scenario_type="road_closure",
            route_change=route_change,
            coverage_change=coverage_change,
            before_metrics=before_metrics,
            after_metrics=after_metrics,
            summary=summary,
        )

    # ------------------------------------------------------------------
    # facility_add / facility_remove / facility_relocate
    # ------------------------------------------------------------------

    async def _simulate_facility_change(
        self,
        graph: nx.MultiDiGraph,
        population,
        baseline_nodes: dict[int, int],
        cutoff_m: float,
        payload: ScenarioRequest,
    ) -> ScenarioResponse:
        after_nodes = set(baseline_nodes.keys())
        removed_node: int | None = None
        added_node: int | None = None

        if payload.scenario_type in ("facility_remove", "facility_relocate"):
            removed_facility = await self.facility_repo.get(payload.facility_id)
            if removed_facility is None:
                raise CityLogicComputationError(f"facility_id {payload.facility_id} not found")
            try:
                removed_node = int(removed_facility.node_id)
            except (TypeError, ValueError) as exc:
                raise CityLogicComputationError(f"facility {payload.facility_id} has no resolvable node_id") from exc
            after_nodes.discard(removed_node)

        if payload.scenario_type in ("facility_add", "facility_relocate"):
            lat, lon = payload.facility_location
            try:
                added_node = int(ox.distance.nearest_nodes(graph, X=lon, Y=lat))
            except Exception as exc:  # noqa: BLE001
                raise CityLogicComputationError(f"could not resolve facility_location to the road network: {exc}") from exc
            after_nodes.add(added_node)

        before_nodes = list(baseline_nodes.keys())
        covered_before, total_pop = self._coverage(graph, population, before_nodes, cutoff_m)
        covered_after, _ = self._coverage(graph, population, list(after_nodes), cutoff_m)
        coverage_change = self._build_coverage_change(covered_before, covered_after, total_pop, payload.coverage_radius_km)

        route_change: RouteChange | None = None
        if payload.origin and payload.destination:
            route_change = await self._dispatch_eta_change(
                graph, before_nodes, list(after_nodes), payload.origin, payload.destination
            )

        before_metrics: dict[str, Any] = {
            "facility_count": len(before_nodes),
            "coverage_fraction": coverage_change.coverage_fraction_before,
        }
        after_metrics: dict[str, Any] = {
            "facility_count": len(after_nodes),
            "coverage_fraction": coverage_change.coverage_fraction_after,
        }
        if route_change:
            before_metrics["dispatch_eta_minutes"] = route_change.eta_minutes_before
            after_metrics["dispatch_eta_minutes"] = route_change.eta_minutes_after

        summary = self._summarize_facility_change(payload.scenario_type, coverage_change, route_change)

        return ScenarioResponse(
            scenario_type=payload.scenario_type,
            route_change=route_change,
            coverage_change=coverage_change,
            before_metrics=before_metrics,
            after_metrics=after_metrics,
            summary=summary,
        )

    async def _dispatch_eta_change(
        self,
        graph: nx.MultiDiGraph,
        before_nodes: list[int],
        after_nodes: list[int],
        origin,
        destination,
    ) -> RouteChange | None:
        """Nearest-facility dispatch ETA to `destination`, before vs. after the facility change."""

        def _nearest_reachable(nodes: list[int]) -> tuple[float, float] | None:
            if not nodes:
                return None
            try:
                dest_node = ox.distance.nearest_nodes(graph, X=destination[1], Y=destination[0])
            except Exception:
                return None
            best = None
            for n in nodes:
                try:
                    length_m = nx.shortest_path_length(graph, n, dest_node, weight="length")
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    continue
                if best is None or length_m < best:
                    best = length_m
            if best is None:
                return None
            return best, round((best / 1000.0 / _AVG_SPEED_KMPH) * 60, 1)

        before = _nearest_reachable(before_nodes)
        after = _nearest_reachable(after_nodes)
        if before is None and after is None:
            return None

        before_km = round(before[0] / 1000.0, 3) if before else None
        after_km = round(after[0] / 1000.0, 3) if after else None
        before_eta = before[1] if before else None
        after_eta = after[1] if after else None

        return RouteChange(
            distance_km_before=before_km,
            distance_km_after=after_km,
            delay_km=round((after_km or 0) - (before_km or 0), 3) if before_km is not None and after_km is not None else None,
            eta_minutes_before=before_eta,
            eta_minutes_after=after_eta,
            eta_delay_minutes=round((after_eta or 0) - (before_eta or 0), 1) if before_eta is not None and after_eta is not None else None,
            status="OK",
        )

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _coverage(graph: nx.MultiDiGraph, population, facility_nodes: list[int], cutoff_m: float) -> tuple[float, float]:
        total_pop = float(population.sum())
        valid_nodes = [n for n in facility_nodes if n in graph.nodes]
        if not valid_nodes:
            return 0.0, total_pop
        lengths = nx.multi_source_dijkstra_path_length(graph, valid_nodes, cutoff=cutoff_m, weight="length")
        covered = 0.0
        for node_id in lengths:
            if node_id in population.index:
                covered += float(population.loc[node_id])
        return covered, total_pop

    @staticmethod
    def _build_coverage_change(covered_before: float, covered_after: float, total_pop: float, radius_km: float) -> CoverageChange:
        frac_before = round(covered_before / total_pop, 4) if total_pop else 0.0
        frac_after = round(covered_after / total_pop, 4) if total_pop else 0.0
        return CoverageChange(
            population_covered_before=round(covered_before, 1),
            population_covered_after=round(covered_after, 1),
            coverage_fraction_before=frac_before,
            coverage_fraction_after=frac_after,
            coverage_fraction_delta=round(frac_after - frac_before, 4),
            total_population=round(total_pop, 1),
            coverage_radius_km=radius_km,
        )

    @staticmethod
    def _summarize_road_closure(route_change: RouteChange, coverage_change: CoverageChange) -> str:
        if route_change.status != "OK":
            return f"Closing this road leaves no alternative route: {route_change.status}."
        parts = [
            f"Closing this road adds {route_change.delay_km} km "
            f"({route_change.eta_delay_minutes} min) of detour on the evaluated route."
        ]
        delta_pct = round(coverage_change.coverage_fraction_delta * 100, 2)
        if delta_pct != 0:
            direction = "reduces" if delta_pct < 0 else "increases"
            parts.append(f"Citywide facility coverage {direction} by {abs(delta_pct)} percentage points.")
        else:
            parts.append("No measurable change to citywide facility coverage.")
        return " ".join(parts)

    @staticmethod
    def _summarize_facility_change(scenario_type: str, coverage_change: CoverageChange, route_change: RouteChange | None) -> str:
        delta_pct = round(coverage_change.coverage_fraction_delta * 100, 2)
        verb = {
            "facility_add": "adding this facility",
            "facility_remove": "removing this facility",
            "facility_relocate": "relocating this facility",
        }[scenario_type]
        direction = "increases" if delta_pct > 0 else ("decreases" if delta_pct < 0 else "does not change")
        parts = [f"{verb.capitalize()} {direction} citywide population coverage by {abs(delta_pct)} percentage points."]
        if route_change and route_change.eta_delay_minutes is not None:
            change_word = "faster" if route_change.eta_delay_minutes < 0 else "slower"
            parts.append(f"Dispatch ETA to the evaluated destination becomes {abs(route_change.eta_delay_minutes)} min {change_word}.")
        return " ".join(parts)
