"""NSI — News Signal Index. The layer our agent feeds.

Every other layer in the model is historical: it can tell you that a stretch of
road has been dangerous for years, but not that something happened there last
night. NSI is the layer that reacts. It is what the four-layer models in
docs/RESEARCH.md do not have.

Unlike the other layers, NSI is **not** stored as a precomputed per-cell value.
It stores the underlying incident records and computes pressure at query time,
because the signal decays continuously — a stabbing three days ago should weigh
less than the same stabbing this morning, and we should not need to re-run the
agent to make that true.

    pressure(cell, t) = Σ  severity_i · spatial_falloff(d_i) · 2^(-age_i / halflife)
    NSI(cell, t)      = 1 - clamp01(pressure / SATURATION)

Spatial falloff exists because news locations are named places, not GPS fixes.
"Near Saket Metro station" is a neighbourhood, not a point, so an incident
spreads its weight over the cells around its geocoded centroid rather than
landing entirely in one 500 m box.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from ..config import PROCESSED_DIR, get_settings
from ..grid import get_grid, haversine_m
from .base import Layer

INCIDENTS_PATH = PROCESSED_DIR / "nsi_incidents.json"

#: Pressure at which a cell is considered maximally risky by news alone. Set so
#: that a couple of severe, recent, well-localised incidents saturate a cell —
#: news is a strong local signal but we do not want one dramatic story to
#: black-hole an entire neighbourhood on its own.
SATURATION = 3.0


@dataclass
class Incident:
    """One geolocated, extracted news incident."""

    id: str
    lat: float
    lon: float
    #: ISO-8601, UTC. When the incident happened (not when it was published,
    #: where the agent could tell the difference).
    occurred_at: str
    severity: float
    category: str
    headline: str
    url: str
    source: str
    #: The agent's own confidence that this is a real, correctly located
    #: incident in Delhi, in [0, 1]. Scales the incident's contribution.
    confidence: float = 1.0
    location_text: str = ""
    cell_id: str | None = None
    #: Free-text note from the extraction step, for auditing bad calls.
    note: str = ""
    tags: list[str] = field(default_factory=list)

    @property
    def occurred_dt(self) -> datetime:
        dt = datetime.fromisoformat(self.occurred_at)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


class NSILayer(Layer):
    name = "nsi"
    confidence = "medium"

    def __init__(self) -> None:
        super().__init__()
        self.incidents: list[Incident] = []
        #: cell_id -> [(incident_index, spatial_weight)]
        self._index: dict[str, list[tuple[int, float]]] = {}

    # -- persistence ------------------------------------------------------

    @property
    def path(self):  # type: ignore[override]
        return INCIDENTS_PATH

    def load(self) -> NSILayer:
        if INCIDENTS_PATH.exists():
            raw = json.loads(INCIDENTS_PATH.read_text(encoding="utf-8"))
            self.incidents = [Incident(**r) for r in raw.get("incidents", [])]
            self._meta = raw.get("meta", {})
            self._loaded = True
            self._build_index()
        return self

    def save_incidents(
        self, incidents: list[Incident], meta: dict | None = None
    ) -> None:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "layer": self.name,
            "meta": {**(meta or {}), "written_at": _utcnow().isoformat()},
            "incidents": [asdict(i) for i in incidents],
        }
        INCIDENTS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.incidents = incidents
        self._meta = payload["meta"]
        self._loaded = True
        self._build_index()

    # -- indexing ---------------------------------------------------------

    def _build_index(self) -> None:
        """Smear each incident over the cells within the blur radius.

        Done once at load rather than per query: the incident set is small
        (tens to low hundreds) but route scoring hits this on every sampled
        point, and recomputing the falloff there would dominate the request.
        """
        settings = get_settings()
        grid = get_grid()
        radius = settings.news_blur_radius_m

        self._index = {}
        # How many cells out we need to walk to cover the blur radius.
        reach = max(1, math.ceil(radius / grid.spec.cell_size_m))

        for idx, inc in enumerate(self.incidents):
            rc = grid.row_col(inc.lat, inc.lon)
            if rc is None:
                continue  # outside Delhi; the agent should have filtered it
            row, col = rc
            inc.cell_id = grid.encode(row, col)

            for dr in range(-reach, reach + 1):
                for dc in range(-reach, reach + 1):
                    r, c = row + dr, col + dc
                    if not (0 <= r < grid.n_rows and 0 <= c < grid.n_cols):
                        continue
                    cid = grid.encode(r, c)
                    clat, clon = grid.cell_center(cid)
                    d = haversine_m(inc.lat, inc.lon, clat, clon)
                    w = _spatial_falloff(d, radius)
                    if w > 0.01:
                        self._index.setdefault(cid, []).append((idx, w))

    # -- query ------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._loaded

    @property
    def coverage(self) -> int:
        return len(self._index)

    def has(self, cell_id: str | None) -> bool:
        # This layer keeps incidents, not per-cell values, so the base
        # implementation's `_values` check would report no data everywhere and
        # silently disqualify NSI from ever explaining a segment.
        return cell_id is not None and cell_id in self._index

    def pressure(self, cell_id: str | None, when: datetime) -> float:
        """Un-normalised, un-inverted news risk pressure for a cell."""
        if cell_id is None or not self._index:
            return 0.0
        entries = self._index.get(cell_id)
        if not entries:
            return 0.0

        settings = get_settings()
        when = when if when.tzinfo else when.replace(tzinfo=UTC)
        halflife = max(settings.news_halflife_days, 0.1)
        max_age = settings.news_max_age_days

        total = 0.0
        for idx, spatial_w in entries:
            inc = self.incidents[idx]
            age_days = (when - inc.occurred_dt).total_seconds() / 86_400.0
            if age_days < 0 or age_days > max_age:
                continue
            decay = 2.0 ** (-age_days / halflife)
            total += inc.severity * inc.confidence * spatial_w * decay
        return total

    def value(self, cell_id: str | None, when: datetime) -> float:
        # No news is genuinely good news here: an area with no recent reported
        # incident gets 1.0, not NEUTRAL. That differs from the other layers on
        # purpose — absence of a *news report* is weak evidence of safety, but
        # this layer only ever penalises, and defaulting it to 0.5 would drag
        # every score in the city down by a constant with no information in it.
        return self.clamp01(1.0 - self.pressure(cell_id, when) / SATURATION)

    def active_incidents(self, cell_id: str | None, when: datetime, limit: int = 3):
        """The incidents actually driving this cell's score, most recent first.

        Used by the explanation layer so a news flag can cite its source rather
        than asserting that something happened.
        """
        if cell_id is None:
            return []
        entries = self._index.get(cell_id, [])
        settings = get_settings()
        when = when if when.tzinfo else when.replace(tzinfo=UTC)

        out = []
        for idx, _w in entries:
            inc = self.incidents[idx]
            age_days = (when - inc.occurred_dt).total_seconds() / 86_400.0
            if 0 <= age_days <= settings.news_max_age_days:
                out.append(inc)
        out.sort(key=lambda i: i.occurred_dt, reverse=True)
        return out[:limit]


def _spatial_falloff(distance_m: float, radius_m: float) -> float:
    """Smooth 1 -> 0 falloff over the blur radius (raised cosine)."""
    if distance_m >= radius_m:
        return 0.0
    return 0.5 * (1.0 + math.cos(math.pi * distance_m / radius_m))


def _utcnow() -> datetime:
    return datetime.now(UTC)
