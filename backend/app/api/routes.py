"""HTTP endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query

from ..config import DEFAULT_WEIGHTS, DELHI_TZ, MODE_WEIGHTS, validate_weights
from ..data import get_layers, reset_layers
from ..explain import (
    annotate_segments,
    compare,
    departure_note,
    fastest_and_safest,
    news_citations,
    route_facts,
    route_highlights,
    route_reasons,
    summarise,
    tradeoff,
)
from ..features import reset_features
from ..grid import get_grid, reset_grid
from ..places import OutsideServiceArea, PlacesError, get_place, get_places_client
from ..pois import CATEGORY_LABELS, get_poi_index, reset_poi_index
from ..routes_api import RoutesError, get_routes_client
from ..schemas import (
    HealthResponse,
    LatLng,
    MapPOI,
    PlaceDetail,
    PlaceSearchResponse,
    PlaceSuggestion,
    POIResponse,
    RouteOut,
    RouteRequest,
    RouteResponse,
)
from ..scoring import departure_options, rank_routes, score_polyline

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["meta"])
def health() -> HealthResponse:
    """Which layers are real, and what the grid looks like.

    Check this first when a score looks wrong. A fresh clone has no layers
    built, so every score comes out near 0.5 — that is the system working
    correctly on no data, not a bug in the model.
    """
    grid = get_grid()
    layers = get_layers()
    client = get_routes_client()

    warnings: list[str] = []
    unavailable = [n for n, s in layers.status().items() if not s["available"]]
    if unavailable:
        warnings.append(
            f"No data for layer(s): {', '.join(sorted(unavailable))}. "
            "Scores fall back to neutral. See scripts/README.md to build them."
        )
    if client.provider == "mock":
        warnings.append(
            "Serving mock routes — GOOGLE_MAPS_API_KEY is not set. Route geometry "
            "is synthetic and does not follow real streets."
        )

    return HealthResponse(
        status="ok",
        provider=client.provider,
        grid={
            "rows": grid.n_rows,
            "cols": grid.n_cols,
            "cells": grid.n_cells,
            "cell_size_m": grid.spec.cell_size_m,
            "bbox": [
                grid.spec.min_lon,
                grid.spec.min_lat,
                grid.spec.max_lon,
                grid.spec.max_lat,
            ],
        },
        layers=layers.status(),  # type: ignore[arg-type]
        warnings=warnings,
    )


@router.post("/routes", response_model=RouteResponse, tags=["routing"])
def compute_routes(req: RouteRequest) -> RouteResponse:
    """Score every alternative route between two points and rank them by safety."""
    when = _delhi_time(req.departure_time)

    mode = req.travel_mode
    try:
        weights = validate_weights(req.weights) if req.weights else dict(MODE_WEIGHTS[mode])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    client = get_routes_client()
    try:
        candidates = client.fetch(req.origin.as_tuple(), req.destination.as_tuple(), mode)
    except RoutesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    scored = []
    for cand in candidates:
        route = score_polyline(
            cand.points,
            when,
            weights=weights,
            mode=mode,
            distance_m=cand.distance_m,
            duration_s=cand.duration_s,
            polyline=cand.polyline,
        )
        route.summary = summarise(route)
        route.reasons = route_reasons(route, when)
        route.news = news_citations(route, when)
        scored.append((cand, route))

    ordered = rank_routes([r for _c, r in scored])
    fastest, safest = fastest_and_safest(ordered)
    labels = {id(r): c.label for c, r in scored}

    now = datetime.now(UTC)
    out: list[RouteOut] = []
    for rank, route in enumerate(ordered):
        options = departure_options(route, when, now)
        out.append(
            RouteOut(
                rank=rank,
                recommended=(rank == 0),
                fastest=route is fastest,
                safest=route is safest,
                label=labels.get(id(route), f"Route {rank + 1}"),
                css=round(route.css, 4),
                band=route.band,
                distance_m=round(route.distance_m, 1),
                duration_s=round(route.duration_s, 1),
                polyline=route.polyline,
                segments=annotate_segments(route),  # type: ignore[arg-type]
                layer_contributions={k: round(v, 4) for k, v in route.layer_contributions.items()},
                worst_stretch=_worst_out(route.worst_stretch),
                coverage=round(route.coverage, 3),
                summary=route.summary,
                reasons=route.reasons,
                news=route.news,  # type: ignore[arg-type]
                highlights=route_highlights(route),
                facts=route_facts(route),
                main_road_share=(
                    round(route.main_road_share, 3) if route.main_road_share is not None else None
                ),
                longest_gap=_gap_out(route.longest_gap),
                open_places=[
                    {"id": p.id, "lat": p.lat, "lng": p.lng, "category": p.category, "name": p.name}
                    for p in route.open_places[:200]
                ],
                longest_isolated=_gap_out(route.longest_isolated),
                farthest_help=_help_out(route.farthest_help),
                cameras=[{"lat": lat, "lng": lng} for lat, lng in route.cameras],
                signals=[{"lat": lat, "lng": lng} for lat, lng in route.signals],
                departure_options=[{**o, "css": round(o["css"], 4)} for o in options],
                departure_note=departure_note(options),
            )
        )

    warnings: list[str] = []
    if client.provider == "mock":
        warnings.append("Mock routing provider — geometry is synthetic.")
    missing = [n for n, s in get_layers().status().items() if not s["available"]]
    if missing:
        warnings.append(f"Layers with no data (scored neutral): {', '.join(sorted(missing))}.")

    return RouteResponse(
        routes=out,
        comparison=compare(ordered[0], ordered[1] if len(ordered) > 1 else None),
        tradeoff=tradeoff(fastest, safest),
        departure_time=when,
        travel_mode=mode,
        provider=client.provider,
        weights=weights,
        warnings=warnings,
    )


@router.get("/cell", tags=["debug"])
def inspect_cell(lat: float, lng: float, at: datetime | None = None) -> dict:
    """Every layer's value for the cell containing a point.

    The fastest way to answer "why is this road red?" — and the first thing to
    reach for when a score looks wrong.
    """
    when = _delhi_time(at)

    grid = get_grid()
    cell_id = grid.cell_id(lat, lng)
    if cell_id is None:
        raise HTTPException(status_code=404, detail="Point is outside the Delhi grid")

    layers = get_layers()
    values = {
        name: {
            "value": round(layer.value(cell_id, when), 4),
            "has_data": layer.has(cell_id),
            "weighted": round(DEFAULT_WEIGHTS[name] * layer.value(cell_id, when), 4),
        }
        for name, layer in layers.as_dict().items()
    }
    css = sum(v["weighted"] for v in values.values())

    center_lat, center_lng = grid.cell_center(cell_id)
    return {
        "cell_id": cell_id,
        "center": {"lat": center_lat, "lng": center_lng},
        "bounds": grid.cell_bounds(cell_id),
        "at": when.isoformat(),
        "css": round(css, 4),
        "layers": values,
        "news": [
            {"headline": i.headline, "url": i.url, "occurred_at": i.occurred_at}
            for i in layers.nsi.active_incidents(cell_id, when, limit=5)
        ],
    }


@router.get("/places/autocomplete", response_model=PlaceSearchResponse, tags=["places"])
def places_autocomplete(
    q: str = Query(min_length=1, max_length=200, description="What the user has typed so far"),
    session: str | None = Query(
        default=None,
        max_length=64,
        description="Random token, the same for every keystroke of one search and for "
        "the GET /places/{place_id} that ends it. Groups them into one billed session.",
    ),
    lat: float | None = Query(default=None, description="Where the user is, for distances"),
    lng: float | None = Query(default=None),
) -> PlaceSearchResponse:
    """Places in Delhi NCR matching a partial query, for the search boxes.

    Misspellings are tolerated: Google's autocomplete corrects them itself,
    and the offline fallback does fuzzy matching.
    """
    client = get_places_client()
    near = (lat, lng) if lat is not None and lng is not None else None
    try:
        suggestions = client.autocomplete(q, session, near)
    except PlacesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return PlaceSearchResponse(
        suggestions=[PlaceSuggestion(**s.__dict__) for s in suggestions],
        provider=client.provider,
    )


@router.get("/places/{place_id}", response_model=PlaceDetail, tags=["places"])
def place_detail(place_id: str, session: str | None = Query(default=None, max_length=64)):
    """Coordinates for a suggestion. 422 if it lies outside the routable area."""
    try:
        place = get_place(place_id, session)
    except OutsideServiceArea as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except PlacesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return PlaceDetail(
        place_id=place.place_id,
        name=place.name,
        address=place.address,
        location=LatLng(lat=place.lat, lng=place.lng),
    )


@router.get("/pois", response_model=POIResponse, tags=["places"])
def map_pois(
    bbox: str = Query(
        description="min_lng,min_lat,max_lng,max_lat of the visible map",
        examples=["77.20,28.60,77.24,28.64"],
    ),
    categories: str | None = Query(
        default=None, description="Comma-separated category keys; all when omitted"
    ),
    limit: int = Query(default=500, ge=1, le=2000),
) -> POIResponse:
    """Hospitals, police, transit, shops and more inside the visible map.

    Display data from OpenStreetMap, built by scripts/fetch_map_pois.py. When
    a view holds more than `limit`, the most safety-relevant categories are
    kept (police, hospitals, transit first) and `truncated` is true.
    """
    try:
        min_lng, min_lat, max_lng, max_lat = (float(v) for v in bbox.split(","))
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="bbox must be four comma-separated numbers"
        ) from exc
    if (max_lat - min_lat) * (max_lng - min_lng) > 1.0:
        raise HTTPException(status_code=422, detail="bbox too large; zoom in")

    wanted = {c.strip() for c in categories.split(",") if c.strip()} if categories else None
    pois, truncated = get_poi_index().in_bbox(min_lng, min_lat, max_lng, max_lat, wanted, limit)
    return POIResponse(
        pois=[
            MapPOI(
                id=p.id,
                lat=p.lat,
                lng=p.lng,
                category=p.category,
                name=p.name,
                address=p.address,
                tags=p.tags,
            )
            for p in pois
        ],
        truncated=truncated,
        categories=CATEGORY_LABELS,
        attribution="© OpenStreetMap contributors",
    )


@router.post("/admin/reload", tags=["meta"])
def reload_layers() -> dict:
    """Re-read layers from disk without restarting.

    The news agent runs out of band and writes new incident files; this is how
    a running server picks them up.
    """
    reset_grid()
    reset_layers()
    reset_poi_index()
    reset_features()
    return {"reloaded": True, "layers": get_layers().status()}


def _delhi_time(when: datetime | None) -> datetime:
    """The request time in Delhi local time; every hourly curve is keyed on it.

    A naive timestamp is taken as UTC. Converting changes how the instant is
    expressed, not the instant, so NSI's age arithmetic is unaffected.
    """
    when = when or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return when.astimezone(DELHI_TZ)


def _gap_out(gap: dict | None) -> dict | None:
    if gap is None:
        return None
    return {
        "length_m": round(gap["length_m"], 1),
        "start": LatLng(lat=gap["start"][0], lng=gap["start"][1]),
        "end": LatLng(lat=gap["end"][0], lng=gap["end"][1]),
    }


def _help_out(found: dict | None) -> dict | None:
    if found is None:
        return None
    p = found["place"]
    return {
        "distance_m": round(found["distance_m"], 1),
        "point": LatLng(lat=found["point"][0], lng=found["point"][1]),
        "place": {"id": p.id, "lat": p.lat, "lng": p.lng, "category": p.category, "name": p.name},
    }


def _worst_out(worst: dict | None) -> dict | None:
    if worst is None:
        return None
    return {
        **worst,
        "start": LatLng(lat=worst["start"][0], lng=worst["start"][1]),
        "end": LatLng(lat=worst["end"][0], lng=worst["end"][1]),
    }
