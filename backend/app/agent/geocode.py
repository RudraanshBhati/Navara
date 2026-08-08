"""Place name -> coordinates, with a persistent cache.

News repeats locations relentlessly — "Karol Bagh" will appear in a dozen
articles a week — so an uncached geocoder would burn the quota re-resolving the
same twenty place names forever. The cache is a plain JSON file; at this scale
anything more is overhead.

Results are biased to the Delhi bounding box and filtered to it afterwards.
Both are needed: `bounds` is only a hint to Google, and without the explicit
post-filter a story about Delhi, Ontario or a "New Delhi restaurant" in another
city will happily geocode to somewhere outside the grid and then be silently
dropped by the NSI indexer with no explanation of why.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from ..config import DELHI_BBOX, PROCESSED_DIR, get_settings

log = logging.getLogger(__name__)

CACHE_PATH = PROCESSED_DIR / "geocode_cache.json"

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = DELHI_BBOX


@dataclass
class GeoResult:
    lat: float
    lon: float
    formatted: str
    #: Google's own precision hint: ROOFTOP > RANGE_INTERPOLATED > GEOMETRIC_CENTER
    #: > APPROXIMATE. A neighbourhood centroid ("APPROXIMATE") is much weaker
    #: evidence than a street address, and we scale confidence by it.
    location_type: str = "APPROXIMATE"

    @property
    def precision_factor(self) -> float:
        return {
            "ROOFTOP": 1.0,
            "RANGE_INTERPOLATED": 0.9,
            "GEOMETRIC_CENTER": 0.75,
            "APPROXIMATE": 0.55,
        }.get(self.location_type, 0.55)


class Geocoder:
    def __init__(self) -> None:
        self._cache: dict[str, dict | None] = {}
        self._load_cache()

    def _load_cache(self) -> None:
        if CACHE_PATH.exists():
            try:
                self._cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("geocode cache corrupt, starting fresh")
                self._cache = {}

    def save_cache(self) -> None:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(self._cache, indent=2), encoding="utf-8")

    def geocode(self, place: str) -> GeoResult | None:
        """Resolve a place name inside Delhi, or None."""
        key = _normalise(place)
        if not key:
            return None

        if key in self._cache:
            cached = self._cache[key]
            return GeoResult(**cached) if cached else None

        result = self._call_api(place)
        # Negative results are cached too — a place name that does not resolve
        # will keep showing up in the news and should not keep costing calls.
        self._cache[key] = result.__dict__ if result else None
        return result

    def _call_api(self, place: str) -> GeoResult | None:
        settings = get_settings()
        if not settings.google_maps_api_key:
            log.warning("GOOGLE_MAPS_API_KEY not set; cannot geocode %r", place)
            return None

        query = place if "delhi" in place.lower() else f"{place}, Delhi, India"
        params = {
            "address": query,
            "key": settings.google_maps_api_key,
            "components": "country:IN|administrative_area:Delhi",
            "bounds": f"{MIN_LAT},{MIN_LON}|{MAX_LAT},{MAX_LON}",
        }
        try:
            r = httpx.get(settings.geocoding_api_url, params=params, timeout=20.0)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("geocode request failed for %r: %s", place, exc)
            return None

        if payload.get("status") != "OK" or not payload.get("results"):
            log.info("geocode miss for %r (status=%s)", place, payload.get("status"))
            return None

        top = payload["results"][0]
        geom = top["geometry"]
        lat = geom["location"]["lat"]
        lon = geom["location"]["lng"]

        if not (MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lon <= MAX_LON):
            log.info("geocode for %r landed outside Delhi (%.4f, %.4f)", place, lat, lon)
            return None

        return GeoResult(
            lat=lat,
            lon=lon,
            formatted=top.get("formatted_address", query),
            location_type=geom.get("location_type", "APPROXIMATE"),
        )


def _normalise(place: str) -> str:
    return " ".join((place or "").lower().split())
