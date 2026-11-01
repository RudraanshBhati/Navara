"""Places to draw on the map, and fuzzy name search over them.

Built by scripts/fetch_map_pois.py from OpenStreetMap. Display data only —
nothing here feeds a safety score (the crowd layer has its own fixed input).

Two jobs:

  in_bbox    what GET /pois returns for the visible map area, highest-priority
             categories first, so a zoomed-out view of Delhi shows police and
             hospitals rather than the first 500 tea stalls.
  search     typo-tolerant name matching, which is what offline place search
             falls back to when there is no Google key. Google's own
             autocomplete already corrects spelling; this keeps "conaught
             place" working without it.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .config import PROCESSED_DIR

log = logging.getLogger(__name__)

MAP_POIS_PATH = PROCESSED_DIR / "map_pois.json"

#: Draw order when a view holds more than the limit. Places that matter when
#: something goes wrong come first.
CATEGORY_PRIORITY = (
    "police",
    "hospital",
    "metro",
    "train",
    "bus",
    "pharmacy",
    "toilets",
    "fuel",
    "atm",
    "food",
    "shop",
)

CATEGORY_LABELS = {
    "police": "Police station",
    "hospital": "Hospital",
    "metro": "Metro station",
    "train": "Railway station",
    "bus": "Bus stop",
    "pharmacy": "Pharmacy",
    "toilets": "Public toilet",
    "fuel": "Petrol pump",
    "atm": "ATM / bank",
    "food": "Restaurant / cafe",
    "shop": "Shop",
}

_METRO_HINTS = ("metro", "subway", "rapid")

#: Spatial bucket size in degrees (~1.1 km). Small enough that a street-level
#: view touches a handful of buckets, large enough that a city view does not
#: touch thousands.
_BUCKET_DEG = 0.01


def classify(tags: dict) -> str | None:
    """One display category for an OSM element, or None to skip it."""
    amenity = tags.get("amenity")
    if amenity == "police":
        return "police"
    if amenity in ("hospital", "clinic"):
        return "hospital"
    if tags.get("railway") == "station":
        blob = " ".join(tags.get(k, "") for k in ("station", "network", "name", "subway")).lower()
        if tags.get("station") == "subway" or any(h in blob for h in _METRO_HINTS):
            return "metro"
        return "train"
    if tags.get("highway") == "bus_stop" or amenity == "bus_station":
        return "bus"
    if amenity == "pharmacy" or tags.get("shop") == "chemist":
        return "pharmacy"
    if amenity == "toilets":
        return "toilets"
    if amenity == "fuel":
        return "fuel"
    if amenity in ("atm", "bank"):
        return "atm"
    if amenity in ("restaurant", "cafe", "fast_food"):
        return "food"
    if tags.get("shop"):
        return "shop"
    return None


@dataclass
class POI:
    id: str
    lat: float
    lng: float
    category: str
    name: str
    tags: dict = field(default_factory=dict)

    @property
    def address(self) -> str:
        t = self.tags
        street = " ".join(p for p in (t.get("addr:housenumber"), t.get("addr:street")) if p)
        parts = [street, t.get("addr:suburb"), t.get("addr:city"), t.get("addr:postcode")]
        return ", ".join(p for p in parts if p)


class POIIndex:
    def __init__(self, pois: list[POI]) -> None:
        self.pois = pois
        self.by_id = {p.id: p for p in pois}
        self._buckets: dict[tuple[int, int], list[POI]] = {}
        for p in pois:
            self._buckets.setdefault(_bucket(p.lat, p.lng), []).append(p)
        # Search only covers places with names; an unnamed bus stop is not
        # something anyone types.
        self._named = [(p, _norm(p.name)) for p in pois if p.name]

    @classmethod
    def load(cls) -> POIIndex:
        if not MAP_POIS_PATH.exists():
            return cls([])
        raw = json.loads(MAP_POIS_PATH.read_text(encoding="utf-8"))
        return cls([POI(**p) for p in raw.get("pois", [])])

    @property
    def available(self) -> bool:
        return bool(self.pois)

    def in_bbox(
        self,
        min_lng: float,
        min_lat: float,
        max_lng: float,
        max_lat: float,
        categories: set[str] | None = None,
        limit: int = 500,
    ) -> tuple[list[POI], bool]:
        """Places inside the box, priority categories first. True if truncated."""
        b0, b1 = _bucket(min_lat, min_lng), _bucket(max_lat, max_lng)
        by_cat: dict[str, list[POI]] = {}
        for bx in range(b0[0], b1[0] + 1):
            for by in range(b0[1], b1[1] + 1):
                for p in self._buckets.get((bx, by), ()):
                    if categories and p.category not in categories:
                        continue
                    if min_lat <= p.lat <= max_lat and min_lng <= p.lng <= max_lng:
                        by_cat.setdefault(p.category, []).append(p)

        out: list[POI] = []
        for cat in CATEGORY_PRIORITY:
            items = by_cat.get(cat, [])
            room = limit - len(out)
            if len(items) > room:
                # Named places before unnamed ones within a category.
                items = sorted(items, key=lambda p: not p.name)[:room]
            out.extend(items)
        total = sum(len(v) for v in by_cat.values())
        return out, total > len(out)

    def search(
        self, query: str, limit: int = 8, near: tuple[float, float] | None = None
    ) -> list[POI]:
        """Named places matching `query`, tolerating typos.

        Exact prefix and substring matches rank first; then fuzzy matches on
        whole names and on individual words, which is what catches "hauz
        khaz" and "conaught". When `near` is given, distance breaks ties.
        """
        q = _norm(query)
        if len(q) < 2:
            return []

        scored: list[tuple[float, POI]] = []
        for p, name in self._named:
            score = _match_score(q, name)
            if score < 0.75:
                continue
            if near is not None:
                km = _km(near, (p.lat, p.lng))
                score -= min(km, 50) / 500  # at most 0.1 for distance
            scored.append((score, p))

        scored.sort(key=lambda sp: -sp[0])
        seen: set[tuple[str, str]] = set()
        out: list[POI] = []
        for _s, p in scored:
            key = (p.name.lower(), p.category)
            if key in seen:  # one "Domino's" per category, not forty
                continue
            seen.add(key)
            out.append(p)
            if len(out) >= limit:
                break
        return out


def _match_score(q: str, name: str) -> float:
    if name.startswith(q):
        return 2.0
    if q in name:
        return 1.6
    best = SequenceMatcher(None, q, name[: len(q) + 3]).ratio()
    q_words = q.split()
    n_words = name.split()
    if q_words and n_words:
        # Every typed word must be close to some word in the name.
        per_word = [max(SequenceMatcher(None, w, n).ratio() for n in n_words) for w in q_words]
        best = max(best, min(per_word))
    return best


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _bucket(lat: float, lng: float) -> tuple[int, int]:
    return int(math.floor(lat / _BUCKET_DEG)), int(math.floor(lng / _BUCKET_DEG))


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat = (a[0] - b[0]) * 111.32
    dlng = (a[1] - b[1]) * 111.32 * math.cos(math.radians(a[0]))
    return math.hypot(dlat, dlng)


_index: POIIndex | None = None


def get_poi_index() -> POIIndex:
    global _index
    if _index is None:
        _index = POIIndex.load()
        log.info("map POIs loaded: %d", len(_index.pois))
    return _index


def reset_poi_index() -> None:
    global _index
    _index = None
