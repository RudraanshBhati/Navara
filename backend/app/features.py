"""Street-level terms: what road you are on, and what is open around you.

The five layers in data/ are per 500 m cell. These two are per route segment,
because they are properties of the street itself — the cell a main road runs
through also contains the lanes behind it, and a petrol pump 400 m away in the
same cell does nothing for you at 1am.

  road   1.0 on a main road (trunk, primary, secondary), less on smaller roads,
         lowest on lanes and internal roads. Main roads are lit, patrolled, and
         have traffic at every hour; an internal lane at night often has none
         of the three. Highways (motorway/trunk) score below arterials for
         walking, where they mean no footpath and fast traffic.
  act    places likely open near this segment at this hour, weighted by kind
         (a police station or 24-hour hospital is a help point; a closed shop
         is nothing). Saturating, so the 30th shop adds little.

Idea credit: the road-type and open-places terms, and the "dark gap" route
metric built on them, follow a night-activity score demonstrated in Aevy TV's
"Mapping UNSAFE streets for women" (Oct 2026). The data and arithmetic here
are our own.

Both are built from OpenStreetMap (scripts/fetch_roads.py,
scripts/fetch_map_pois.py). Absent data files leave the terms ungrounded:
they score NEUTRAL and are never blamed in an explanation, exactly like an
unbuilt layer.

Four more measures are reported beside the score and do not enter it:

  cameras     mapped CCTV near the route. Measured November 2026: 3,261 outdoor
              cameras in OSM, 45% of them in 20 of Delhi's 10,700 cells, and
              any at all in only 189 of 3,496 densely populated cells. A score
              term would reward wherever a volunteer happened to map, so a
              camera can be cited where mapped and absence says nothing.
  signals     mapped traffic signals on the route, for riders and drivers: the
              places they must stop. Neither good nor bad on its own (a junction
              is busy and visible, and a stopped rider is exposed), so it is
              shown as a fact and given no sign in the score.
  isolation   stretches with no detected homes and no mapped place. The crowd
              layer already scores population; adding it again would count it
              twice.
  help        straight-line distance to the nearest police station, hospital
              or petrol pump likely open. The open-places term already rewards
              those within 75 m; this is the distance when none is that close.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .config import PROCESSED_DIR, RAW_DIR
from .grid import haversine_m
from .pois import POI, get_poi_index

log = logging.getLogger(__name__)

ROADS_PATH = PROCESSED_DIR / "roads_major.json"
CAMERAS_PATH = PROCESSED_DIR / "cameras.json"
SIGNALS_PATH = PROCESSED_DIR / "signals.json"
POPULATION_PATH = RAW_DIR / "population_cells.json"

NEUTRAL = 0.5

#: Within this distance of a main road's centreline, a route point is on it.
#: Google and OSM both trace centrelines; a divided arterial's two carriageways
#: sit 10–25 m apart.
ON_ROAD_M = 30.0

#: How far from the route an open place still counts as "around you".
ACTIVITY_RADIUS_M = 75.0

#: Any mapped place (open or not) within this distance shows that OSM covers
#: the street, so "nothing open" there is evidence rather than a blank map.
MAPPED_RADIUS_M = 200.0

#: A camera this close to the route can plausibly see it.
CAMERA_RADIUS_M = 50.0

#: A signal this close to the route's centreline is on it. A road running under
#: a flyover can still match, so the count is "about this many", not exact.
SIGNAL_RADIUS_M = 30.0

#: OSM maps one signal per approach, so one junction is often four or more
#: nodes. Nodes closer than this count as one junction.
JUNCTION_MERGE_M = 120.0

#: Below this many detected residents in a 500 m cell, nobody lives along the
#: road. Not zero: the settlement layer scatters a few people into empty land.
#: 2,066 of 10,700 cells fall under it, against 1,593 with none at all.
ISOLATED_PEOPLE = 25.0

# -- road term -------------------------------------------------------------

#: Road class -> value, per travel mode. Walking prefers arterials to
#: highways; vehicles prefer the bigger the better.
ROAD_VALUES: dict[str, dict[str, float]] = {
    "walk": {"motorway": 0.6, "trunk": 0.8, "primary": 1.0, "secondary": 1.0, "tertiary": 0.75},
    "two_wheeler": {
        "motorway": 0.9,
        "trunk": 1.0,
        "primary": 1.0,
        "secondary": 0.95,
        "tertiary": 0.75,
    },
    "drive": {"motorway": 1.0, "trunk": 1.0, "primary": 1.0, "secondary": 0.9, "tertiary": 0.7},
}
MINOR_ROAD_VALUE = 0.3
MAIN_CLASSES = frozenset({"motorway", "trunk", "primary", "secondary"})

_ROAD_BUCKET = 0.005  # degrees, ~500 m


class RoadIndex:
    def __init__(self, roads: list[dict]) -> None:
        self._buckets: dict[tuple[int, int], list[tuple[str, float, float, float, float]]] = {}
        for road in roads:
            pts = road["pts"]
            for (a_lat, a_lng), (b_lat, b_lng) in zip(pts, pts[1:], strict=False):
                for key in _buckets_for_segment(a_lat, a_lng, b_lat, b_lng):
                    self._buckets.setdefault(key, []).append(
                        (road["cls"], a_lat, a_lng, b_lat, b_lng)
                    )

    @classmethod
    def load(cls) -> RoadIndex | None:
        if not ROADS_PATH.exists():
            return None
        return cls(json.loads(ROADS_PATH.read_text(encoding="utf-8"))["roads"])

    def road_class(self, lat: float, lng: float) -> str | None:
        """The biggest main-road class within ON_ROAD_M, or None for a minor road."""
        best: str | None = None
        rank = {
            c: i for i, c in enumerate(("tertiary", "secondary", "primary", "trunk", "motorway"))
        }
        for cls, a_lat, a_lng, b_lat, b_lng in self._buckets.get(_bucket(lat, lng), ()):
            if best is not None and rank[cls] <= rank[best]:
                continue
            if _point_segment_m(lat, lng, a_lat, a_lng, b_lat, b_lng) <= ON_ROAD_M:
                best = cls
        return best


def road_value(road_class: str | None, mode: str) -> float:
    if road_class is None:
        return MINOR_ROAD_VALUE
    return ROAD_VALUES.get(mode, ROAD_VALUES["walk"])[road_class]


# -- activity term -----------------------------------------------------------

#: How much one open place of each kind says "someone is here who could help".
ACTIVITY_WEIGHTS = {
    "police": 3.0,
    "hospital": 2.5,  # adjusted down for clinics in place_weight()
    "fuel": 1.5,  # staffed forecourt, lit, usually late
    "metro": 2.0,
    "train": 2.0,
    "food": 1.2,
    "pharmacy": 1.0,
    "shop": 1.0,
    "bus": 0.6,
    "atm": 0.4,
}

#: Opening hours assumed when OSM has none — 92% of NCR places. A judgement,
#: stated here so it can be argued with: police stations and hospitals are
#: staffed round the clock; the rest keep typical Delhi trading hours.
#: (start_hour, end_hour), end exclusive, may wrap past midnight.
DEFAULT_HOURS: dict[str, tuple[int, int]] = {
    "police": (0, 24),
    "hospital": (0, 24),
    "clinic": (9, 21),
    "fuel": (6, 23),
    "metro": (6, 23),
    "train": (0, 24),
    "food": (11, 23),
    "pharmacy": (9, 22),
    "shop": (10, 21),
    "bus": (6, 23),
    "atm": (0, 24),
}

#: Activity at which the term reaches ~0.63: about three ordinary open places.
ACTIVITY_SCALE = 3.0


def is_clinic(poi: POI) -> bool:
    t = poi.tags
    return poi.category == "hospital" and (
        t.get("amenity") == "clinic" or t.get("healthcare") in ("clinic", "doctor", "dentist")
    )


def place_weight(poi: POI) -> float:
    if is_clinic(poi):
        return 1.0
    return ACTIVITY_WEIGHTS.get(poi.category, 0.0)


def is_open(poi: POI, when: datetime) -> bool:
    """Open at `when`: from its OSM hours if they parse, else the category default."""
    parsed = parse_open(poi.tags.get("opening_hours", ""), when)
    if parsed is not None:
        return parsed
    key = "clinic" if is_clinic(poi) else poi.category
    start, end = DEFAULT_HOURS.get(key, (10, 20))
    h = when.hour
    return start <= h < end if start < end else (h >= start or h < end)


def activity_value(score: float) -> float:
    return 1.0 - math.exp(-score / ACTIVITY_SCALE)


def is_help_point(poi: POI) -> bool:
    """Somewhere staffed to run to. A clinic is a closed room at night."""
    return poi.category in ("police", "fuel") or (poi.category == "hospital" and not is_clinic(poi))


@dataclass
class SegmentFeatures:
    road_class: str | None
    road_grounded: bool
    open_places: list[POI]
    activity_grounded: bool
    #: Everything within ACTIVITY_RADIUS_M, open or not, so a route can be
    #: re-scored for another hour without searching again.
    nearby_places: list[POI]


class FeatureIndex:
    """Both street-level terms, ready to query per segment."""

    def __init__(self) -> None:
        self.roads = RoadIndex.load()
        self.pois = get_poi_index()
        self.cameras = _load_points(CAMERAS_PATH, "cameras")
        self.signals = _load_points(SIGNALS_PATH, "signals")
        self.population = _load_population()
        self.help_points = [p for p in self.pois.pois if is_help_point(p)]
        log.info(
            "street features: roads=%s pois=%d cameras=%d signals=%d population=%s",
            self.roads is not None,
            len(self.pois.pois),
            len(self.cameras),
            len(self.signals),
            self.population is not None,
        )

    def at(self, lat: float, lng: float, when: datetime) -> SegmentFeatures:
        road_class = self.roads.road_class(lat, lng) if self.roads else None
        open_places: list[POI] = []
        nearby_places: list[POI] = []
        mapped = False
        if self.pois.available:
            dlat = MAPPED_RADIUS_M / 111_320
            dlng = dlat / math.cos(math.radians(lat))
            nearby, _ = self.pois.in_bbox(
                lng - dlng, lat - dlat, lng + dlng, lat + dlat, limit=10_000
            )
            for p in nearby:
                if place_weight(p) <= 0:
                    continue
                d = haversine_m(lat, lng, p.lat, p.lng)
                if d <= MAPPED_RADIUS_M:
                    mapped = True
                if d <= ACTIVITY_RADIUS_M:
                    nearby_places.append(p)
                    if is_open(p, when):
                        open_places.append(p)
        return SegmentFeatures(
            road_class=road_class,
            road_grounded=self.roads is not None,
            open_places=open_places,
            # Measured only where OSM has mapped something nearby. With nothing
            # mapped, "nothing open" and "nobody mapped this street" look the
            # same, and the second is common in Delhi. Measured on the first
            # night-time run, before this check: the term sat at ~0.05 of its
            # weight on every route and became the top-blamed cause on all of
            # them — an artefact of map coverage, not of the streets.
            activity_grounded=mapped,
            nearby_places=nearby_places,
        )

    def is_empty_land(self, cell_id: str | None) -> bool | None:
        """Whether nobody lives in this cell; None where that is unknown."""
        if self.population is None or cell_id is None:
            return None
        return self.population.get(cell_id, 0.0) < ISOLATED_PEOPLE

    def cameras_near(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """Each mapped camera within CAMERA_RADIUS_M of any of `points`, once."""
        return _points_near(self.cameras, points, CAMERA_RADIUS_M)

    def signals_on(self, points: list[tuple[float, float]]) -> list[tuple[float, float]]:
        """One point per signalled junction within SIGNAL_RADIUS_M of `points`."""
        junctions: list[tuple[float, float]] = []
        for lat, lng in _points_near(self.signals, points, SIGNAL_RADIUS_M):
            if all(haversine_m(lat, lng, a, b) > JUNCTION_MERGE_M for a, b in junctions):
                junctions.append((lat, lng))
        return junctions

    def farthest_from_help(self, points: list[tuple[float, float]], when: datetime) -> dict | None:
        """The point on a route farthest from any help point open at `when`.

        Returns {distance_m, point, place}: the point, and the place nearest to
        it. None when no help point is mapped or open.
        """
        places = [p for p in self.help_points if is_open(p, when)]
        if not places or not points:
            return None
        coords = np.array([(p.lat, p.lng) for p in places])
        dist = _distances_m(np.asarray(points), coords)
        nearest = dist.argmin(axis=1)
        worst = int(dist[np.arange(len(points)), nearest].argmax())
        return {
            "distance_m": float(dist[worst, nearest[worst]]),
            "point": points[worst],
            "place": places[int(nearest[worst])],
        }


def _load_points(path, key: str) -> np.ndarray:
    if not path.exists():
        return np.empty((0, 2))
    points = json.loads(path.read_text(encoding="utf-8"))[key]
    return np.array(points, dtype=float).reshape(-1, 2)


def _points_near(
    candidates: np.ndarray, points: list[tuple[float, float]], radius_m: float
) -> list[tuple[float, float]]:
    """The candidates within `radius_m` of any of `points`, each once."""
    if not len(candidates) or not points:
        return []
    route = np.asarray(points)
    pad = radius_m / 111_320 * 2  # generous for longitude at Delhi's latitude
    lo, hi = route.min(axis=0) - pad, route.max(axis=0) + pad
    box = candidates[((candidates >= lo) & (candidates <= hi)).all(axis=1)]
    if not len(box):
        return []
    near = _distances_m(route, box).min(axis=0) <= radius_m
    return [(float(lat), float(lng)) for lat, lng in box[near]]


def _load_population() -> dict[str, float] | None:
    if not POPULATION_PATH.exists():
        return None
    return json.loads(POPULATION_PATH.read_text(encoding="utf-8"))["cells"]


def _distances_m(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Pairwise metres, rows of `a` by rows of `b`, on a flat local projection."""
    k = math.cos(math.radians(float(a[:, 0].mean())))
    dlat = a[:, 0, None] - b[:, 0]
    dlng = (a[:, 1, None] - b[:, 1]) * k
    return np.hypot(dlat, dlng) * 111_320


_features: FeatureIndex | None = None


def get_features() -> FeatureIndex:
    global _features
    if _features is None:
        _features = FeatureIndex()
    return _features


def reset_features() -> None:
    global _features
    _features = None


# -- opening_hours ------------------------------------------------------------

_DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\+?$")


def parse_open(spec: str, when: datetime) -> bool | None:
    """Whether an OSM opening_hours value says open at `when`.

    Covers the forms that make up nearly all of Delhi's tagged places —
    "24/7", "Mo-Sa 10:00-21:00; Su off", "09:00-13:00,14:00-20:00", ranges
    running past midnight. Anything else (public holidays, months, sunrise)
    returns None so the caller falls back to the category default rather than
    guessing from half a rule.
    """
    spec = (spec or "").strip()
    if not spec:
        return None
    if spec == "24/7":
        return True

    day = _DAYS[when.weekday()]
    yesterday = _DAYS[(when.weekday() - 1) % 7]
    minute = when.hour * 60 + when.minute
    verdict: bool | None = None
    understood = False

    for rule in (r.strip() for r in spec.split(";")):
        if not rule:
            continue
        days_part, times_part = _split_rule(rule)
        if days_part is None:
            return None
        days = _expand_days(days_part) if days_part else set(_DAYS)
        if days is None:
            return None
        if times_part.lower() in ("off", "closed"):
            if day in days:
                verdict = False
            understood = True
            continue

        ranges = []
        for part in times_part.split(","):
            m = _TIME_RANGE.match(part.strip())
            if not m:
                return None
            h1, m1, h2, m2 = (int(x) for x in m.groups())
            ranges.append((h1 * 60 + m1, h2 * 60 + m2))
        understood = True

        # A later rule naming today overrides an earlier one, per the spec.
        if day in days:
            verdict = any(_in_range(minute, a, b) for a, b in ranges)
        # Yesterday's range that runs past midnight still covers early today.
        if (
            yesterday in days
            and verdict is not True
            and any(b <= a and minute < b for a, b in ranges)
        ):
            verdict = True

    if not understood:
        return None
    return bool(verdict)


def _split_rule(rule: str) -> tuple[str | None, str]:
    """('Mo-Fr', '09:00-17:00'), ('', '09:00-17:00'), or (None, ...) if unknown."""
    if rule[0].isdigit():
        return "", rule
    head, _, rest = rule.partition(" ")
    if not rest:
        return None, rule
    return head, rest.strip()


def _expand_days(text: str) -> set[str] | None:
    out: set[str] = set()
    for chunk in text.split(","):
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            if a not in _DAYS or b not in _DAYS:
                return None
            i, j = _DAYS.index(a), _DAYS.index(b)
            span = range(i, j + 1) if i <= j else [*range(i, 7), *range(0, j + 1)]
            out.update(_DAYS[k] for k in span)
        elif chunk in _DAYS:
            out.add(chunk)
        else:
            return None  # PH, SH, months, week numbers...
    return out


def _in_range(minute: int, start: int, end: int) -> bool:
    if end <= start:  # runs past midnight: covers start..24:00 today
        return minute >= start
    return start <= minute < end


# -- geometry ------------------------------------------------------------------


def _bucket(lat: float, lng: float) -> tuple[int, int]:
    return int(math.floor(lat / _ROAD_BUCKET)), int(math.floor(lng / _ROAD_BUCKET))


def _buckets_for_segment(a_lat, a_lng, b_lat, b_lng):
    """Every bucket the segment's box touches, padded by one so a point just
    across a bucket edge still finds the road."""
    (r0, c0), (r1, c1) = (
        _bucket(min(a_lat, b_lat), min(a_lng, b_lng)),
        _bucket(max(a_lat, b_lat), max(a_lng, b_lng)),
    )
    for r in range(r0 - 1, r1 + 2):
        for c in range(c0 - 1, c1 + 2):
            yield (r, c)


def _point_segment_m(lat, lng, a_lat, a_lng, b_lat, b_lng) -> float:
    """Distance from a point to a short segment, on a local flat projection."""
    k = math.cos(math.radians(lat))
    px, py = (lng - a_lng) * k, lat - a_lat
    sx, sy = (b_lng - a_lng) * k, b_lat - a_lat
    seg2 = sx * sx + sy * sy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, (px * sx + py * sy) / seg2))
    dx, dy = px - t * sx, py - t * sy
    return math.hypot(dx, dy) * 111_320
