"""Request and response models — the contract the frontend codes against.

Field names here are the API. Changing one is a breaking change for whoever is
building the map, so treat renames as a PR that needs their review. Everything
is documented because these descriptions become the OpenAPI schema at /docs,
which is where a frontend contributor will actually look.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .config import SERVICE_BBOX

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = SERVICE_BBOX

TravelMode = Literal["walk", "two_wheeler", "drive"]


class LatLng(BaseModel):
    lat: float = Field(description="Latitude, WGS84", examples=[28.6139])
    lng: float = Field(description="Longitude, WGS84", examples=[77.2090])

    @field_validator("lat")
    @classmethod
    def _lat_in_delhi(cls, v: float) -> float:
        if not (MIN_LAT <= v <= MAX_LAT):
            raise ValueError(f"latitude {v} is outside the Delhi NCR service area")
        return v

    @field_validator("lng")
    @classmethod
    def _lng_in_delhi(cls, v: float) -> float:
        if not (MIN_LON <= v <= MAX_LON):
            raise ValueError(f"longitude {v} is outside the Delhi NCR service area")
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
    travel_mode: TravelMode = Field(
        default="walk",
        description=(
            "How the trip is made. Changes both the routes (Google's WALK, "
            "TWO_WHEELER or DRIVE) and the weights: road type counts for more "
            "on a vehicle, open places and crowd for more on foot."
        ),
    )
    weights: dict[str, float] | None = Field(
        default=None,
        description=(
            "Override the CSS weight vector. Must contain every term "
            "(cip, cds, trc, ntls, nsi, act, road) and sum to 1.0. Intended for "
            "tuning and evaluation, not for end users."
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
    reason: str | None = Field(
        default=None,
        description=(
            "Layer key that most explains this stretch, or null when no layer "
            "with data for these cells can explain it. The stretch is real "
            "either way — do not invent a cause when this is null."
        ),
    )
    start: LatLng
    end: LatLng


class Gap(BaseModel):
    length_m: float
    start: LatLng
    end: LatLng


class OpenPlace(BaseModel):
    id: str
    lat: float
    lng: float
    category: str
    name: str


class RouteFact(BaseModel):
    key: str = Field(
        description="'open' | 'main_road' | 'gap' | 'isolated' | 'help' | 'signals' | 'cameras'"
    )
    value: str = Field(description="The figure, formatted: '2.2 km', '62%', '4'")
    label: str = Field(description="What the figure counts, to sit beside it")
    text: str = Field(description="The whole fact as one sentence, for tooltips and screen readers")


class FarthestHelp(BaseModel):
    distance_m: float = Field(description="Straight-line metres to `place` from `point`")
    point: LatLng = Field(description="Where on the route help is farthest away")
    place: OpenPlace = Field(description="The nearest help point likely open, from `point`")


class DepartureOption(BaseModel):
    at: datetime
    css: float
    band: str
    current: bool = Field(description="True for the requested time; its css matches the route's")


class RouteOut(BaseModel):
    rank: int = Field(description="0 is the recommended route")
    recommended: bool
    fastest: bool = Field(default=False, description="Shortest travel time of the alternatives")
    safest: bool = Field(default=False, description="Highest safety score of the alternatives")
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
    highlights: list[str] = Field(
        default_factory=list,
        description="Short factual lines for the route card: places passed, main-road share, longest gap",
    )
    facts: list[RouteFact] = Field(
        default_factory=list,
        description="The same facts as `highlights`, split so they can be laid out as stats",
    )
    main_road_share: float | None = Field(
        default=None,
        description="Fraction of length on main roads; null when road data is not built",
    )
    longest_gap: Gap | None = Field(
        default=None,
        description="Longest stretch with no mapped place likely open at this hour; draw it on the map",
    )
    open_places: list[OpenPlace] = Field(
        default_factory=list,
        description="Places likely open along the route at departure time (capped at 200)",
    )
    longest_isolated: Gap | None = Field(
        default=None,
        description="Longest stretch with no detected homes and no mapped place; draw it on the map",
    )
    farthest_help: FarthestHelp | None = Field(
        default=None,
        description="Where the route is farthest from a police station, hospital or petrol pump likely open",
    )
    cameras: list[LatLng] = Field(
        default_factory=list,
        description=(
            "Mapped outdoor CCTV within 50 m of the route. Not in the score. An empty "
            "list means none are mapped, not that none exist; do not present it as a lack."
        ),
    )
    signals: list[LatLng] = Field(
        default_factory=list,
        description=(
            "One point per mapped signalled junction on the route. Two-wheeler and car only; "
            "not in the score. Empty means none are mapped, not that none exist."
        ),
    )
    departure_options: list[DepartureOption] = Field(
        default_factory=list,
        description="This route scored at nearby times to leave, earliest first; past times omitted",
    )
    departure_note: str = Field(
        default="", description="One sentence on what leaving at another of those times would change"
    )


class RouteResponse(BaseModel):
    routes: list[RouteOut] = Field(
        description=(
            "Recommended first: the shortest of the routes scoring within 0.02 of "
            "the best. The rest follow safest-first."
        )
    )
    comparison: str = Field(
        description="One sentence on why the top route was picked over the next best"
    )
    tradeoff: str = Field(
        default="",
        description="One sentence on what the safest route costs and gains against the fastest",
    )
    departure_time: datetime
    travel_mode: TravelMode
    provider: str = Field(description="'google' for real routes, 'mock' for the offline stand-in")
    weights: dict[str, float]
    warnings: list[str] = Field(
        default_factory=list,
        description="Anything that should qualify how much to trust this result",
    )


class PlaceSuggestion(BaseModel):
    place_id: str = Field(description="Pass to GET /places/{place_id} to get coordinates")
    name: str = Field(description="Short name, e.g. 'Cyber Hub'")
    address: str = Field(description="The rest of the address, for telling similar names apart")
    distance_m: float | None = Field(
        default=None, description="Straight-line metres from `lat`/`lng`, when those were sent"
    )
    kind: str = Field(
        default="place",
        description="'police' | 'hospital' | 'transit' | 'food' | 'shop' | 'place' — for the icon",
    )


class PlaceSearchResponse(BaseModel):
    suggestions: list[PlaceSuggestion]
    provider: str = Field(
        description="'google' for live place search, 'local' for the offline landmark list"
    )


class PlaceDetail(BaseModel):
    place_id: str
    name: str
    address: str
    location: LatLng


class MapPOI(BaseModel):
    id: str
    lat: float
    lng: float
    category: str = Field(description="One of the keys of GET /pois `categories`")
    name: str = Field(description="Empty when OpenStreetMap has no name, e.g. many bus stops")
    address: str
    tags: dict[str, str] = Field(
        default_factory=dict,
        description="Selected OSM tags: opening_hours, phone, website, operator, ...",
    )


class POIResponse(BaseModel):
    pois: list[MapPOI]
    truncated: bool = Field(description="More places exist in this view than were returned")
    categories: dict[str, str] = Field(description="category key -> display label")
    attribution: str


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
