"""Typeable place search for the origin and destination boxes.

Proxied through the backend rather than called from the browser so the Google
key never ships to a client, and so search is restricted to the same service
area that /routes accepts — a suggestion the router would then reject with a
422 is worse than no suggestion.

Like routing, it degrades to an offline stand-in when no key is configured:
a fixed list of Delhi NCR landmarks, matched by substring. Every response says
which one answered.

Two calls per pick, per Google's session model: autocomplete while typing,
then details for the one chosen. Passing the same `session` token to both bills
the whole search as a single session instead of one charge per keystroke.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from .config import SERVICE_BBOX, get_settings
from .grid import haversine_m
from .pois import CATEGORY_LABELS, get_poi_index

log = logging.getLogger(__name__)

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = SERVICE_BBOX

LOCAL_PREFIX = "local:"
POI_PREFIX = "osm:"


@dataclass
class Suggestion:
    place_id: str
    name: str
    address: str
    #: Straight-line distance from the `near` point, when one was given.
    distance_m: float | None = None
    #: A coarse kind for the icon: "police", "hospital", "transit", "food",
    #: "shop", or "place". Google's own types are mapped onto these.
    kind: str = "place"


@dataclass
class Place:
    place_id: str
    name: str
    address: str
    lat: float
    lng: float


class PlacesError(RuntimeError):
    pass


class OutsideServiceArea(PlacesError):
    pass


def in_service_area(lat: float, lng: float) -> bool:
    return MIN_LAT <= lat <= MAX_LAT and MIN_LON <= lng <= MAX_LON


class GooglePlacesClient:
    provider = "google"

    def autocomplete(
        self, query: str, session: str | None = None, near: tuple[float, float] | None = None
    ) -> list[Suggestion]:
        settings = get_settings()
        body: dict = {
            "input": query,
            "includedRegionCodes": ["in"],
            # A restriction, not a bias: a bias still returns "Sector 18,
            # Chandigarh" for "sector 18", which the router then refuses.
            "locationRestriction": {
                "rectangle": {
                    "low": {"latitude": MIN_LAT, "longitude": MIN_LON},
                    "high": {"latitude": MAX_LAT, "longitude": MAX_LON},
                }
            },
        }
        if session:
            body["sessionToken"] = session
        if near:
            # Only adds a distance to each result. A restriction and a bias
            # cannot be combined, and the restriction is the one that keeps
            # suggestions routable.
            body["origin"] = {"latitude": near[0], "longitude": near[1]}

        payload = self._request("POST", settings.places_autocomplete_url, json=body)
        out: list[Suggestion] = []
        for item in payload.get("suggestions", []):
            pred = item.get("placePrediction")
            if not pred:
                continue
            fmt = pred.get("structuredFormat", {})
            name = fmt.get("mainText", {}).get("text") or pred.get("text", {}).get("text", "")
            out.append(
                Suggestion(
                    place_id=pred["placeId"],
                    name=name,
                    address=fmt.get("secondaryText", {}).get("text", ""),
                    distance_m=pred.get("distanceMeters"),
                    kind=_kind_from_google(pred.get("types", [])),
                )
            )
        return out

    def details(self, place_id: str, session: str | None = None) -> Place:
        settings = get_settings()
        params = {"sessionToken": session} if session else None
        payload = self._request(
            "GET",
            f"{settings.places_details_url}/{place_id}",
            params=params,
            # Billed by field. Location and address sit in the cheapest tier;
            # displayName does not, and the frontend already has the name from
            # the suggestion it clicked.
            field_mask="location,formattedAddress",
        )
        loc = payload.get("location") or {}
        if "latitude" not in loc:
            raise PlacesError("Place has no location")
        address = payload.get("formattedAddress", "")
        return Place(
            place_id=place_id,
            name=address.split(",")[0] if address else place_id,
            address=address,
            lat=loc["latitude"],
            lng=loc["longitude"],
        )

    def _request(
        self,
        method: str,
        url: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        field_mask: str | None = None,
    ) -> dict:
        settings = get_settings()
        headers = {"X-Goog-Api-Key": settings.google_maps_api_key}
        if field_mask:
            headers["X-Goog-FieldMask"] = field_mask
        try:
            r = httpx.request(method, url, json=json, params=params, headers=headers, timeout=15.0)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as exc:
            raise PlacesError(
                f"Places API returned {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise PlacesError(f"Places API request failed: {exc}") from exc


class LocalPlacesClient:
    """Offline stand-in, so search works from `git clone` with no key.

    Searches the landmark list and, when scripts/fetch_map_pois.py has been
    run, every named OpenStreetMap place on the map — with typo tolerance, so
    "conaught place" still finds Connaught Place.
    """

    provider = "local"

    def autocomplete(
        self,
        query: str,
        session: str | None = None,  # noqa: ARG002
        near: tuple[float, float] | None = None,
    ) -> list[Suggestion]:
        q = query.strip().lower()
        if not q:
            return []
        hits = [p for p in LANDMARKS if q in p.name.lower() or q in p.address.lower()]
        # Names that start with the query first: "sa" should offer Saket before
        # Hauz Khas, whose address happens to contain "sa".
        hits.sort(key=lambda p: (not p.name.lower().startswith(q), p.name))
        out = [
            Suggestion(p.place_id, p.name, p.address, _distance(near, p.lat, p.lng))
            for p in hits[:4]
        ]

        for poi in get_poi_index().search(query, limit=8 - len(out), near=near):
            out.append(
                Suggestion(
                    place_id=f"{POI_PREFIX}{poi.id}",
                    name=poi.name,
                    address=poi.address or CATEGORY_LABELS[poi.category],
                    distance_m=_distance(near, poi.lat, poi.lng),
                    kind=_kind_from_category(poi.category),
                )
            )
        return out

    def details(self, place_id: str, session: str | None = None) -> Place:  # noqa: ARG002
        if place_id.startswith(POI_PREFIX):
            poi = get_poi_index().by_id.get(place_id[len(POI_PREFIX) :])
            if poi is None:
                raise PlacesError(f"Unknown place {place_id!r}")
            return Place(place_id, poi.name, poi.address, poi.lat, poi.lng)
        for p in LANDMARKS:
            if p.place_id == place_id:
                return p
        raise PlacesError(f"Unknown place {place_id!r}")


def get_places_client():
    """Real client when a key is configured, the landmark list otherwise."""
    if get_settings().google_maps_api_key:
        return GooglePlacesClient()
    return LocalPlacesClient()


def get_place(place_id: str, session: str | None = None) -> Place:
    """Resolve a suggestion to coordinates, refusing anything the router would."""
    # A local id can come back after a key is added (a stale suggestion list in
    # an open tab), so route it by prefix rather than by the current client.
    local = place_id.startswith((LOCAL_PREFIX, POI_PREFIX))
    client = LocalPlacesClient() if local else get_places_client()
    place = client.details(place_id, session)
    if not in_service_area(place.lat, place.lng):
        raise OutsideServiceArea(f"{place.address or place.name} is outside the Delhi NCR area")
    return place


def _distance(near: tuple[float, float] | None, lat: float, lng: float) -> float | None:
    if near is None:
        return None
    return round(haversine_m(near[0], near[1], lat, lng))


#: Google place types -> the icon kinds the frontend draws. First match wins.
_GOOGLE_KINDS = (
    ("police", "police"),
    ("hospital", "hospital"),
    ("doctor", "hospital"),
    ("pharmacy", "hospital"),
    ("subway_station", "transit"),
    ("transit_station", "transit"),
    ("train_station", "transit"),
    ("bus_station", "transit"),
    ("bus_stop", "transit"),
    ("restaurant", "food"),
    ("cafe", "food"),
    ("food", "food"),
    ("shopping_mall", "shop"),
    ("store", "shop"),
)


def _kind_from_google(types: list[str]) -> str:
    for google_type, kind in _GOOGLE_KINDS:
        if google_type in types:
            return kind
    return "place"


def _kind_from_category(category: str) -> str:
    return {
        "police": "police",
        "hospital": "hospital",
        "pharmacy": "hospital",
        "metro": "transit",
        "train": "transit",
        "bus": "transit",
        "food": "food",
        "shop": "shop",
    }.get(category, "place")


def _landmark(slug: str, name: str, address: str, lat: float, lng: float) -> Place:
    return Place(f"{LOCAL_PREFIX}{slug}", name, address, lat, lng)


#: Approximate landmark centres, typed by hand. Good enough to pick a route
#: without a key; not a gazetteer.
LANDMARKS: list[Place] = [
    _landmark("cp", "Connaught Place", "New Delhi", 28.6315, 77.2167),
    _landmark("ndls", "New Delhi Railway Station", "Paharganj, New Delhi", 28.6430, 77.2194),
    _landmark("india-gate", "India Gate", "Kartavya Path, New Delhi", 28.6129, 77.2295),
    _landmark("chandni-chowk", "Chandni Chowk", "Old Delhi", 28.6506, 77.2303),
    _landmark("kashmere-gate", "Kashmere Gate ISBT", "Delhi", 28.6676, 77.2285),
    _landmark("karol-bagh", "Karol Bagh", "Central Delhi", 28.6519, 77.1909),
    _landmark("north-campus", "Delhi University North Campus", "Delhi", 28.6880, 77.2100),
    _landmark("aiims", "AIIMS", "Ansari Nagar, New Delhi", 28.5672, 77.2100),
    _landmark("lajpat-nagar", "Lajpat Nagar Market", "South Delhi", 28.5677, 77.2433),
    _landmark("hauz-khas", "Hauz Khas Village", "South Delhi", 28.5535, 77.1940),
    _landmark("nehru-place", "Nehru Place", "South Delhi", 28.5494, 77.2519),
    _landmark("saket", "Saket", "South Delhi", 28.5245, 77.2066),
    _landmark("qutub", "Qutub Minar", "Mehrauli, New Delhi", 28.5245, 77.1855),
    _landmark("rajouri", "Rajouri Garden", "West Delhi", 28.6424, 77.1220),
    _landmark("dwarka-21", "Dwarka Sector 21", "South West Delhi", 28.5523, 77.0583),
    _landmark("cyber-hub", "Cyber Hub", "DLF Cyber City, Gurugram", 28.4950, 77.0890),
    _landmark("mg-road-ggn", "MG Road", "Gurugram", 28.4796, 77.0800),
    _landmark("noida-18", "Sector 18", "Noida", 28.5700, 77.3260),
    _landmark("botanical-garden", "Botanical Garden", "Noida", 28.5640, 77.3340),
    _landmark("pari-chowk", "Pari Chowk", "Greater Noida", 28.4660, 77.5130),
    _landmark("vaishali", "Vaishali", "Ghaziabad", 28.6450, 77.3390),
    _landmark("faridabad-nit", "NIT Faridabad", "Faridabad", 28.3920, 77.3010),
]
