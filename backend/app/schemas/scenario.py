"""Request/response schemas for `POST /simulate/scenario`."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Coordinate = tuple[float, float]  # (lat, lon)

ScenarioType = Literal["road_closure", "facility_add", "facility_remove", "facility_relocate"]


class ScenarioRequest(BaseModel):
    scenario_type: ScenarioType

    # Optional route to evaluate before/after the scenario (used by every
    # scenario type as an illustrative "how does my commute/dispatch change" probe).
    origin: Coordinate | None = Field(default=None, description="[lat, lon]")
    destination: Coordinate | None = Field(default=None, description="[lat, lon]")

    # road_closure
    edge_to_close: tuple[int, int] | None = Field(default=None, description="(u, v) OSM node id pair")

    # facility_add / facility_relocate (new location)
    facility_location: Coordinate | None = Field(default=None, description="[lat, lon] of the new/relocated facility")
    facility_type: str = Field(default="GENERIC", max_length=64)

    # facility_remove / facility_relocate (existing facility being removed/moved)
    facility_id: int | None = None

    # Coverage model
    coverage_radius_km: float = Field(default=2.5, gt=0, le=50)

    @model_validator(mode="after")
    def _validate(self) -> "ScenarioRequest":
        if self.scenario_type == "road_closure" and not self.edge_to_close:
            raise ValueError("edge_to_close is required for scenario_type='road_closure'")
        if self.scenario_type in ("road_closure",) and (not self.origin or not self.destination):
            raise ValueError("origin and destination are required for scenario_type='road_closure'")
        if self.scenario_type in ("facility_add", "facility_relocate") and not self.facility_location:
            raise ValueError(f"facility_location is required for scenario_type='{self.scenario_type}'")
        if self.scenario_type in ("facility_remove", "facility_relocate") and self.facility_id is None:
            raise ValueError(f"facility_id is required for scenario_type='{self.scenario_type}'")
        return self


class RouteChange(BaseModel):
    distance_km_before: float | None = None
    distance_km_after: float | None = None
    delay_km: float | None = None
    eta_minutes_before: float | None = None
    eta_minutes_after: float | None = None
    eta_delay_minutes: float | None = None
    route_coordinates_before: list[list[float]] | None = None
    route_coordinates_after: list[list[float]] | None = None
    status: str = "OK"


class CoverageChange(BaseModel):
    population_covered_before: float
    population_covered_after: float
    coverage_fraction_before: float
    coverage_fraction_after: float
    coverage_fraction_delta: float
    total_population: float
    coverage_radius_km: float


class ScenarioResponse(BaseModel):
    scenario_type: ScenarioType
    route_change: RouteChange | None = None
    coverage_change: CoverageChange | None = None
    before_metrics: dict
    after_metrics: dict
    summary: str
