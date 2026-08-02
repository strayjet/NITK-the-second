"""Response schemas for `/heatmap`."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

HeatmapLayerName = Literal["population", "accidents", "closures"]


class GeoJSONGeometry(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: list[float]  # [lon, lat] — GeoJSON order


class GeoJSONFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: GeoJSONGeometry
    properties: dict[str, Any]


class GeoJSONFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[GeoJSONFeature] = Field(default_factory=list)


class HeatmapResponse(BaseModel):
    layers: dict[HeatmapLayerName, GeoJSONFeatureCollection]
    generated_at: str
    warnings: list[str] = Field(default_factory=list)
