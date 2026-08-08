"""HTTP endpoints."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from ..config import DEFAULT_WEIGHTS, validate_weights
from ..data import get_layers, reset_layers
from ..explain import annotate_segments, compare, news_citations, route_reasons, summarise
from ..grid import get_grid, reset_grid
from ..routes_api import RoutesError, get_routes_client
from ..schemas import (
    HealthResponse,
    LatLng,
    RouteOut,
    RouteRequest,
    RouteResponse,
)
from ..scoring import rank_routes, score_polyline

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
    when = req.departure_time or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)

    try:
        weights = validate_weights(req.weights) if req.weights else dict(DEFAULT_WEIGHTS)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    client = get_routes_client()
    try:
        candidates = client.fetch(req.origin.as_tuple(), req.destination.as_tuple())
    except RoutesError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    scored = []
    for cand in candidates:
        route = score_polyline(
            cand.points,
            when,
            weights=weights,
            distance_m=cand.distance_m,
            duration_s=cand.duration_s,
            polyline=cand.polyline,
        )
        route.summary = summarise(route)
        route.reasons = route_reasons(route, when)
        route.news = news_citations(route, when)
        scored.append((cand, route))

    ordered = rank_routes([r for _c, r in scored])
    labels = {id(r): c.label for c, r in scored}

    out: list[RouteOut] = []
    for rank, route in enumerate(ordered):
        out.append(
            RouteOut(
                rank=rank,
                recommended=(rank == 0),
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
        departure_time=when,
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
    when = at or datetime.now(UTC)
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)

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


@router.post("/admin/reload", tags=["meta"])
def reload_layers() -> dict:
    """Re-read layers from disk without restarting.

    The news agent runs out of band and writes new incident files; this is how
    a running server picks them up.
    """
    reset_grid()
    reset_layers()
    return {"reloaded": True, "layers": get_layers().status()}


def _worst_out(worst: dict | None) -> dict | None:
    if worst is None:
        return None
    return {
        **worst,
        "start": LatLng(lat=worst["start"][0], lng=worst["start"][1]),
        "end": LatLng(lat=worst["end"][0], lng=worst["end"][1]),
    }
