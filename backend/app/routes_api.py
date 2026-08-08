"""Google Routes API wrapper, polyline decoding, and an offline stand-in.

The stand-in matters more than it sounds. Anyone working on the frontend needs
routes on screen, and gating that behind a billed Google Cloud key means the
map cannot be developed until someone's card is on file. `MockRoutesClient`
returns deterministic, geographically plausible Delhi walking routes with no
key and no network, so the frontend is unblocked from `git clone`.

It is selected automatically when GOOGLE_MAPS_API_KEY is empty, and every
response says which client produced it — so a demo can never quietly be running
on invented routes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import httpx

from .config import get_settings

log = logging.getLogger(__name__)


@dataclass
class RouteCandidate:
    """One raw alternative from the routing provider, before scoring."""

    points: list[tuple[float, float]]  # (lat, lon)
    distance_m: float
    duration_s: float
    polyline: str
    label: str = ""


class RoutesError(RuntimeError):
    pass


class GoogleRoutesClient:
    provider = "google"

    def fetch(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        travel_mode: str = "WALK",
    ) -> list[RouteCandidate]:
        settings = get_settings()
        if not settings.google_maps_api_key:
            raise RoutesError("GOOGLE_MAPS_API_KEY is not set")

        body = {
            "origin": {"location": {"latLng": {"latitude": origin[0], "longitude": origin[1]}}},
            "destination": {
                "location": {"latLng": {"latitude": destination[0], "longitude": destination[1]}}
            },
            "travelMode": travel_mode,
            "computeAlternativeRoutes": True,
            "languageCode": "en-IN",
            "units": "METRIC",
        }
        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": settings.google_maps_api_key,
            # Routes API bills by the fields you ask for, so ask narrowly.
            "X-Goog-FieldMask": (
                "routes.duration,routes.distanceMeters,"
                "routes.polyline.encodedPolyline,routes.description"
            ),
        }

        try:
            r = httpx.post(settings.routes_api_url, json=body, headers=headers, timeout=30.0)
            r.raise_for_status()
            payload = r.json()
        except httpx.HTTPStatusError as exc:
            raise RoutesError(
                f"Routes API returned {exc.response.status_code}: {exc.response.text[:300]}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RoutesError(f"Routes API request failed: {exc}") from exc

        routes = payload.get("routes", [])
        if not routes:
            raise RoutesError("Routes API returned no routes for this origin/destination")

        out: list[RouteCandidate] = []
        for i, route in enumerate(routes[: settings.max_alternatives]):
            encoded = route.get("polyline", {}).get("encodedPolyline", "")
            out.append(
                RouteCandidate(
                    points=decode_polyline(encoded),
                    distance_m=float(route.get("distanceMeters", 0)),
                    duration_s=_parse_duration(route.get("duration", "0s")),
                    polyline=encoded,
                    label=route.get("description", "") or f"Route {i + 1}",
                )
            )
        return out


class MockRoutesClient:
    """Offline stand-in. Deterministic, no key, no network.

    Generates one direct route plus two detours that bow off to either side, so
    the ranking, colouring, and comparison logic all have something real to
    chew on. The geometry is synthetic — it does not follow actual streets —
    which is exactly why every response carries `provider: "mock"`.
    """

    provider = "mock"

    def fetch(
        self,
        origin: tuple[float, float],
        destination: tuple[float, float],
        travel_mode: str = "WALK",  # noqa: ARG002
    ) -> list[RouteCandidate]:
        out: list[RouteCandidate] = []
        for i, (bow, label) in enumerate(
            [(0.0, "Direct"), (0.35, "Northern detour"), (-0.35, "Southern detour")]
        ):
            points = _bowed_path(origin, destination, bow, steps=60)
            distance = sum(
                _haversine(a, b) for a, b in zip(points, points[1:], strict=False)
            )
            out.append(
                RouteCandidate(
                    points=points,
                    distance_m=distance,
                    duration_s=distance / 1.35,  # ~4.9 km/h walking
                    polyline=encode_polyline(points),
                    label=label,
                )
            )
            if i >= get_settings().max_alternatives - 1:
                break
        return out


def get_routes_client():
    """Real client when a key is configured, mock otherwise."""
    if get_settings().google_maps_api_key:
        return GoogleRoutesClient()
    log.warning("GOOGLE_MAPS_API_KEY not set — serving mock routes")
    return MockRoutesClient()


# ---------------------------------------------------------------------------
# Encoded polyline codec (Google's algorithm, precision 5).
# ---------------------------------------------------------------------------


def decode_polyline(encoded: str) -> list[tuple[float, float]]:
    """Decode to [(lat, lon), ...]."""
    points: list[tuple[float, float]] = []
    index = lat = lon = 0

    while index < len(encoded):
        for is_lat in (True, False):
            shift = result = 0
            while True:
                if index >= len(encoded):
                    return points
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else result >> 1
            if is_lat:
                lat += delta
            else:
                lon += delta
        points.append((lat / 1e5, lon / 1e5))

    return points


def encode_polyline(points: list[tuple[float, float]]) -> str:
    """Encode [(lat, lon), ...] back into Google's format."""
    out: list[str] = []
    prev_lat = prev_lon = 0

    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lon = ilat, ilon

    return "".join(out)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_duration(value: str) -> float:
    """Routes API returns protobuf durations like '843s'."""
    try:
        return float(str(value).rstrip("s"))
    except ValueError:
        return 0.0


def _bowed_path(
    origin: tuple[float, float],
    destination: tuple[float, float],
    bow: float,
    steps: int,
) -> list[tuple[float, float]]:
    """A great-circle-ish path bent perpendicular to the direct line."""
    lat1, lon1 = origin
    lat2, lon2 = destination
    dlat, dlon = lat2 - lat1, lon2 - lon1
    # Perpendicular offset, scaled by how far apart the endpoints are.
    plat, plon = -dlon * bow, dlat * bow

    points = []
    for i in range(steps + 1):
        t = i / steps
        arc = math.sin(math.pi * t)  # zero at both ends, max in the middle
        points.append((lat1 + dlat * t + plat * arc, lon1 + dlon * t + plon * arc))
    return points


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    from .grid import haversine_m

    return haversine_m(a[0], a[1], b[0], b[1])
