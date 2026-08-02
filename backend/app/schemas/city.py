"""Request/response schemas for `/city/*` endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

Coordinate = tuple[float, float]  # (lat, lon)


def _validate_lat_lon(value: Coordinate) -> Coordinate:
    lat, lon = value
    if not (-90.0 <= lat <= 90.0):
        raise ValueError(f"latitude {lat} out of range [-90, 90]")
    if not (-180.0 <= lon <= 180.0):
        raise ValueError(f"longitude {lon} out of range [-180, 180]")
    return value


class RouteRequest(BaseModel):
    origin: Coordinate = Field(..., description="[lat, lon]")
    destination: Coordinate = Field(..., description="[lat, lon]")

    @field_validator("origin", "destination")
    @classmethod
    def _validate_coords(cls, value: Coordinate) -> Coordinate:
        return _validate_lat_lon(value)


class RouteResponse(BaseModel):
    distance_km: float
    route_coordinates: list[list[float]]


class ClosureSimulationRequest(BaseModel):
    origin: Coordinate = Field(..., description="[lat, lon]")
    destination: Coordinate = Field(..., description="[lat, lon]")
    edge_to_close: tuple[int, int] = Field(
        ..., description="(u, v) OSM node ID pair identifying the edge to remove"
    )

    @field_validator("origin", "destination")
    @classmethod
    def _validate_coords(cls, value: Coordinate) -> Coordinate:
        return _validate_lat_lon(value)


class ClosureSimulationResponse(BaseModel):
    status: str
    route_before: list[int] | None = None
    length_before_km: float | None = None
    route_after: list[int] | None = None
    length_after_km: float | None = None
    delay_km: float | None = None
    route_before_coordinates: list[list[float]] | None = None
    route_after_coordinates: list[list[float]] | None = None


class NearestEdgeRequest(BaseModel):
    lat: float = Field(..., description="Latitude of the map click")
    lon: float = Field(..., description="Longitude of the map click")

    @field_validator("lat")
    @classmethod
    def _validate_lat(cls, value: float) -> float:
        if not (-90.0 <= value <= 90.0):
            raise ValueError(f"latitude {value} out of range [-90, 90]")
        return value

    @field_validator("lon")
    @classmethod
    def _validate_lon(cls, value: float) -> float:
        if not (-180.0 <= value <= 180.0):
            raise ValueError(f"longitude {value} out of range [-180, 180]")
        return value


class NearestEdgeResponse(BaseModel):
    node_u: int
    node_v: int
    point_u: list[float]
    point_v: list[float]
    geometry: list[list[float]]
    distance_to_point_m: float


class RoadEdgeItem(BaseModel):
    edge_id: str
    node_u: int
    node_v: int
    point_u: list[float]
    point_v: list[float]
    geometry: list[list[float]]
    length_m: float
    name: str | None = None
    highway: str | None = None
    blocked: bool = Field(default=False, description="Whether this road is currently closed via Block Road")


class RoadNetworkResponse(BaseModel):
    edges: list[RoadEdgeItem]


class BlockRoadRequest(BaseModel):
    edge: tuple[int, int] = Field(..., description="(u, v) OSM node ID pair identifying the road edge to block")


class UnblockRoadRequest(BaseModel):
    edge: tuple[int, int] = Field(..., description="(u, v) OSM node ID pair identifying the road edge to unblock")


class BlockedRoadItem(BaseModel):
    node_u: int
    node_v: int
    geometry: list[list[float]]


class ClosedRoadsResponse(BaseModel):
    blocked_edges: list[BlockedRoadItem]


class FacilityRecommendationRequest(BaseModel):
    n_candidates: int | None = Field(
        default=None, ge=1, description="How many random candidate nodes to evaluate. Defaults to server config."
    )
    # Free-text with a documented fallback: any value not present in the
    # facility catalog (see GET /city/facility-types) is accepted but simply
    # gets no spacing enforcement / catalog label+color, and defaults to the
    # catalog's default 4km spacing via city_logic.get_min_spacing_km().
    facility_type: str = Field(default="hospital", max_length=64)
    name: str | None = Field(default=None, max_length=255)
    persist: bool = Field(default=True, description="Whether to save the recommendation to the database.")


class NearbyFacilityItem(BaseModel):
    lat: float
    lon: float
    distance_km: float


class FacilityRecommendationResponse(BaseModel):
    facility_id: int | None = None
    status: str = "OK"
    detail: str | None = None
    node_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    cost: float | None = None
    n_candidates: int
    facility_type: str | None = None
    label: str | None = None
    color: str | None = None
    min_spacing_km: float | None = None
    existing_facilities_considered: int | None = None
    # Display-only figures for the recommendation UI (see
    # city_logic._compute_recommendation_display_stats); do not affect
    # site-selection logic.
    population_served: float | None = None
    coverage_improvement_pct: float | None = None
    confidence_score: float | None = None
    nearby_facilities: list[NearbyFacilityItem] | None = None


class FacilityTypeInfo(BaseModel):
    value: str
    label: str
    color: str
    min_spacing_km: float


class FacilityTypesResponse(BaseModel):
    facility_types: list[FacilityTypeInfo]


class RoadSuggestionRequest(BaseModel):
    sample_size: int = Field(default=100, ge=2, le=1000)
    min_straight_line_m: float = Field(default=200, ge=0)
    max_straight_line_m: float = Field(default=2000, gt=0)
    top_n: int = Field(default=10, ge=1, le=100)
    persist: bool = Field(
        default=False,
        description="Road-gap suggestions are exploratory candidates, not committed infrastructure — not persisted by default.",
    )


class RoadSuggestionItem(BaseModel):
    node_u: int
    node_v: int
    point_u: list[float]
    point_v: list[float]
    straight_line_m: float
    current_network_dist_m: float
    gap_ratio: float
    distance_saved_m: float
    impact_score: float
    current_path_coordinates: list[list[float]]


class RoadSuggestionResponse(BaseModel):
    suggestions: list[RoadSuggestionItem]
