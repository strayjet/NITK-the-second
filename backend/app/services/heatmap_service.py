"""
HeatmapService — builds GeoJSON heatmap layers from three inputs:

1. **Population density** — sampled from `CityService.node_population`
   (the same population-per-graph-node data used by routing/facility
   recommendation), so the heatmap always reflects the same population
   model the rest of the app reasons about.
2. **Accident frequency** — incidents persisted in Postgres, bucketed onto a
   coarse lat/lon grid and counted.
3. **Closure impact** — persisted `RouteAnalysis` rows of type `CLOSURE`,
   weighted by how much detour distance (`delay_km`) each closure caused.

Each layer is returned as an independent GeoJSON `FeatureCollection` of
`Point` features with a normalized `weight` in [0, 1] plus the raw value, so
the frontend can feed each straight into a Leaflet heat-layer / circle-marker
layer.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Incident, RouteAnalysis
from app.schemas.heatmap import GeoJSONFeature, GeoJSONFeatureCollection, GeoJSONGeometry, HeatmapResponse
from app.services.city_service import CityService

logger = logging.getLogger(__name__)

# Grid resolution for bucketing accident points, in decimal degrees.
# ~0.003 deg is roughly 330m at the equator — coarse enough to aggregate
# nearby detections, fine enough to stay meaningful at city scale.
_ACCIDENT_GRID_DEG = 0.003

_DEFAULT_LIMIT = 500


class HeatmapService:
    def __init__(self, city_service: CityService, db: AsyncSession) -> None:
        self.city_service = city_service
        self.db = db

    async def build(
        self,
        layers: list[str],
        limit: int = _DEFAULT_LIMIT,
    ) -> HeatmapResponse:
        result: dict[str, GeoJSONFeatureCollection] = {}
        warnings: list[str] = []

        if "population" in layers:
            try:
                result["population"] = self._population_layer(limit)
            except Exception as exc:  # noqa: BLE001
                logger.exception("population heatmap layer failed")
                warnings.append(f"population layer unavailable: {exc}")
                result["population"] = GeoJSONFeatureCollection()

        if "accidents" in layers:
            result["accidents"] = await self._accident_layer(limit)

        if "closures" in layers:
            result["closures"] = await self._closure_layer(limit)

        return HeatmapResponse(
            layers=result,
            generated_at=datetime.now(timezone.utc).isoformat(),
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Population density
    # ------------------------------------------------------------------

    def _population_layer(self, limit: int) -> GeoJSONFeatureCollection:
        if not self.city_service.ready or self.city_service.graph is None or self.city_service.node_population is None:
            raise RuntimeError(self.city_service.init_error or "city routing engine is not initialized yet")

        graph = self.city_service.graph
        population = self.city_service.node_population

        # Take the top-`limit` populated nodes rather than every node in the
        # graph, both to bound response size and because zero/near-zero
        # population nodes add no signal to a density heatmap.
        top = population.sort_values(ascending=False).head(limit)
        max_pop = float(top.iloc[0]) if len(top) else 0.0

        features = []
        for node_id, pop in top.items():
            if node_id not in graph.nodes:
                continue
            node = graph.nodes[node_id]
            weight = round(float(pop) / max_pop, 4) if max_pop > 0 else 0.0
            features.append(
                GeoJSONFeature(
                    geometry=GeoJSONGeometry(coordinates=[node["x"], node["y"]]),
                    properties={"layer": "population", "population": round(float(pop), 2), "weight": weight, "node_id": str(node_id)},
                )
            )
        return GeoJSONFeatureCollection(features=features)

    # ------------------------------------------------------------------
    # Accident frequency
    # ------------------------------------------------------------------

    async def _accident_layer(self, limit: int) -> GeoJSONFeatureCollection:
        stmt = select(Incident.lat, Incident.lng).where(Incident.lat.is_not(None), Incident.lng.is_not(None))
        rows = (await self.db.execute(stmt)).all()

        buckets: dict[tuple[float, float], int] = defaultdict(int)
        for lat, lng in rows:
            key = (
                round(lat / _ACCIDENT_GRID_DEG) * _ACCIDENT_GRID_DEG,
                round(lng / _ACCIDENT_GRID_DEG) * _ACCIDENT_GRID_DEG,
            )
            buckets[key] += 1

        if not buckets:
            return GeoJSONFeatureCollection()

        max_count = max(buckets.values())
        top_buckets = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)[:limit]

        features = [
            GeoJSONFeature(
                geometry=GeoJSONGeometry(coordinates=[lng, lat]),
                properties={
                    "layer": "accidents",
                    "count": count,
                    "weight": round(count / max_count, 4),
                },
            )
            for (lat, lng), count in top_buckets
        ]
        return GeoJSONFeatureCollection(features=features)

    # ------------------------------------------------------------------
    # Closure impact
    # ------------------------------------------------------------------

    async def _closure_layer(self, limit: int) -> GeoJSONFeatureCollection:
        stmt = (
            select(RouteAnalysis)
            .where(RouteAnalysis.analysis_type == "CLOSURE", RouteAnalysis.delay_km.is_not(None))
            .order_by(RouteAnalysis.created_at.desc())
            .limit(limit)
        )
        rows = (await self.db.execute(stmt)).scalars().all()
        if not rows:
            return GeoJSONFeatureCollection()

        max_delay = max((r.delay_km or 0.0) for r in rows) or 1.0

        features = []
        for r in rows:
            # Use the midpoint between origin and destination as a proxy for
            # "where the closure's impact was felt" — the exact closed-edge
            # geometry isn't persisted, only its node ids.
            mid_lat = (r.origin_lat + r.dest_lat) / 2
            mid_lon = (r.origin_lon + r.dest_lon) / 2
            features.append(
                GeoJSONFeature(
                    geometry=GeoJSONGeometry(coordinates=[mid_lon, mid_lat]),
                    properties={
                        "layer": "closures",
                        "delay_km": round(r.delay_km or 0.0, 3),
                        "weight": round((r.delay_km or 0.0) / max_delay, 4),
                        "closed_edge": [r.closed_edge_u, r.closed_edge_v],
                    },
                )
            )
        return GeoJSONFeatureCollection(features=features)
