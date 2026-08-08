"""Request and response models — the contract the frontend codes against.

Field names here are the API. Changing one is a breaking change for whoever is
building the map, so treat renames as a PR that needs their review. Everything
is documented because these descriptions become the OpenAPI schema at /docs,
which is where a frontend contributor will actually look.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from .config import DELHI_BBOX

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = DELHI_BBOX


class LatLng(BaseModel):
    lat: float = Field(description="Latitude, WGS84", examples=[28.6139])
    lng: float = Field(description="Longitude, WGS84", examples=[77.2090])

    @field_validator("lat")
    @classmethod
    def _lat_in_delhi(cls, v: float) -> float:
        if not (MIN_LAT - 0.2 <= v <= MAX_LAT + 0.2):
            raise ValueError(f"latitude {v} is outside the Delhi service area")
        return v

    @field_validator("lng")
    @classmethod
    def _lng_in_delhi(cls, v: float) -> float:
        if not (MIN_LON - 0.2 <= v <= MAX_LON + 0.2):
            raise ValueError(f"longitude {v} is outside the Delhi service area")
        return v

    def as_tuple(self) -> tuple[float, float]:
        return self.lat, self.lng


class RouteRequest(BaseModel):
    origin: LatLng
    destination: LatLng
    departure_time: datetime | None = Field(
        default=None,
        description=(
            "When the trip starts, ISO-8601. Defaults to now. This materially "
            "changes the result — lighting and crowd layers are time-dependent, "
            "so the same trip scores differently at 2pm and 2am."
        ),
    )
    weights: dict[str, float] | None = Field(
        default=None,
        description=(
            "Override the CSS weight vector. Must contain all five layers "
            "(cip, cds, trc, ntls, nsi) and sum to 1.0. Intended for tuning "
            "and evaluation, not for end users."
        ),
    )


class SegmentOut(BaseModel):
    start: LatLng
    end: LatLng
    css: float = Field(description="Safety score for this segment, 0-1, higher is safer")
    band: str = Field(description="'safe' | 'caution' | 'risk' — drives the map colour")
    flag: str | None = Field(
        default=None,
        description="Short reason this segment is not green, or null if it is fine",
    )
    cell_id: str | None = None


class NewsItem(BaseModel):
    headline: str
    url: str
    source: str
    occurred_at: str
    category: str
    location: str
    confidence: float = Field(
        description="How much the agent trusts this extraction, 0-1. Show it; do not hide it."
    )


class WorstStretch(BaseModel):
    length_m: float
    css: float
    band: str
    reason: str = Field(description="Layer key that most explains this stretch")
    start: LatLng
    end: LatLng


class RouteOut(BaseModel):
    rank: int = Field(description="0 is the recommended route")
    recommended: bool
    label: str
    css: float
    band: str
    distance_m: float
    duration_s: float
    polyline: str = Field(description="Google-encoded polyline, precision 5")
    segments: list[SegmentOut]
    layer_contributions: dict[str, float] = Field(
        description="Per-layer weighted contribution averaged over the route"
    )
    worst_stretch: WorstStretch | None = None
    coverage: float = Field(description="Fraction of the route inside cells we have real data for")
    summary: str
    reasons: list[str] = Field(description="Why-panel bullets, strongest first")
    news: list[NewsItem] = Field(default_factory=list)


class RouteResponse(BaseModel):
    routes: list[RouteOut] = Field(description="Ranked safest-first")
    comparison: str = Field(
        description="One sentence on why the top route was picked over the next best"
    )
    departure_time: datetime
    provider: str = Field(description="'google' for real routes, 'mock' for the offline stand-in")
    weights: dict[str, float]
    warnings: list[str] = Field(
        default_factory=list,
        description="Anything that should qualify how much to trust this result",
    )


class LayerStatus(BaseModel):
    available: bool
    confidence: str
    cells_covered: int
    meta: dict = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str
    provider: str
    grid: dict
    layers: dict[str, LayerStatus]
    warnings: list[str] = Field(default_factory=list)
