"""The CSS model.

    CSS(segment) = Σ_layer  w_layer · value_layer(cell, time)

A weighted linear sum, on purpose. The deep-learning route-safety models in
docs/RESEARCH.md score better on held-out data and then have to bolt SHAP on
afterwards to say anything about *why*. Here attribution is exact and free:
each layer's contribution is literally a term in the sum, so "this stretch is
amber because it is unlit" is arithmetic, not interpretation. Given that the
product's entire claim is explaining its choice to someone deciding whether to
walk down a road at night, that trade is worth making.

Two ideas do the real work:

  weighted contribution   w_i · v_i           — what this layer added
  weighted deficit        w_i · (1 - v_i)     — what this layer took away

The deficit is what drives explanations. A segment's flag is whichever layer
has the largest deficit, because that is the term actually dragging the score
down — not the largest contribution, which on a good segment is just whichever
layer has the biggest weight.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .config import DEFAULT_WEIGHTS, band_for, validate_weights
from .data import LayerRegistry, get_layers
from .grid import Grid, get_grid, haversine_m, interpolate


@dataclass
class ScoredSegment:
    """One sampled piece of a route, with its score fully broken down."""

    start: tuple[float, float]  # (lat, lon)
    end: tuple[float, float]
    length_m: float
    cell_id: str | None
    css: float
    band: str
    #: layer -> w_i * v_i
    contributions: dict[str, float]
    #: layer -> w_i * (1 - v_i)
    deficits: dict[str, float]
    #: layer -> raw v_i, kept for debugging and the layer-detail panel
    raw: dict[str, float]
    #: Layers that actually hold data for this cell. A layer with no data sits
    #: at NEUTRAL, which produces a deficit of half its weight out of nothing —
    #: large enough to out-rank every real layer and win any "what is wrong with
    #: this road" contest. Nothing may blame a layer outside this set.
    grounded: frozenset[str] = field(default_factory=frozenset)

    @property
    def dominant_deficit(self) -> str:
        return max(self.deficits, key=lambda k: self.deficits[k])

    @property
    def grounded_deficits(self) -> dict[str, float]:
        """Deficits that are evidence rather than absence of evidence."""
        return {n: d for n, d in self.deficits.items() if n in self.grounded}

    @property
    def midpoint(self) -> tuple[float, float]:
        return (
            (self.start[0] + self.end[0]) / 2,
            (self.start[1] + self.end[1]) / 2,
        )


@dataclass
class ScoredRoute:
    """A candidate route with an aggregate score and its breakdown."""

    segments: list[ScoredSegment]
    css: float
    band: str
    distance_m: float
    duration_s: float
    polyline: str
    #: layer -> mean weighted contribution across the route
    layer_contributions: dict[str, float] = field(default_factory=dict)
    #: The lowest-scoring contiguous run — what a user would actually worry about.
    worst_stretch: dict | None = None
    #: Fraction of route length that fell inside cells with real data.
    coverage: float = 0.0
    summary: str = ""
    reasons: list[str] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def resample(points: list[tuple[float, float]], spacing_m: float) -> list[tuple[float, float]]:
    """Re-cut a polyline into points at roughly even spacing.

    Google's polylines are vertex-dense at corners and sparse on straights, so
    scoring raw vertices would weight junctions far more heavily than the long
    stretches between them — exactly backwards for a model about where you walk.
    """
    if len(points) < 2:
        return list(points)

    out: list[tuple[float, float]] = [points[0]]
    carry = 0.0

    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:], strict=False):
        seg_len = haversine_m(lat1, lon1, lat2, lon2)
        if seg_len <= 0:
            continue

        pos = spacing_m - carry
        while pos < seg_len:
            out.append(interpolate(lat1, lon1, lat2, lon2, pos / seg_len))
            pos += spacing_m
        carry = (carry + seg_len) % spacing_m

    if out[-1] != points[-1]:
        out.append(points[-1])
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def score_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    when: datetime,
    weights: dict[str, float],
    layers: LayerRegistry,
    grid: Grid,
) -> ScoredSegment:
    """Score one segment at its midpoint.

    Midpoint rather than start point: a 100 m segment can straddle a cell
    boundary, and sampling at the start systematically attributes the segment
    to whichever cell it is leaving rather than the one it mostly runs through.
    """
    mid_lat = (start[0] + end[0]) / 2
    mid_lon = (start[1] + end[1]) / 2
    cell_id = grid.cell_id(mid_lat, mid_lon)

    raw: dict[str, float] = {}
    contributions: dict[str, float] = {}
    deficits: dict[str, float] = {}

    layer_map = layers.as_dict()
    grounded: set[str] = set()
    for name, weight in weights.items():
        layer = layer_map[name]
        v = layer.value(cell_id, when)
        raw[name] = v
        contributions[name] = weight * v
        deficits[name] = weight * (1.0 - v)
        if layer.has(cell_id):
            grounded.add(name)

    css = sum(contributions.values())
    return ScoredSegment(
        start=start,
        end=end,
        length_m=haversine_m(*start, *end),
        cell_id=cell_id,
        css=css,
        band=band_for(css),
        contributions=contributions,
        deficits=deficits,
        raw=raw,
        grounded=frozenset(grounded),
    )


def score_polyline(
    points: list[tuple[float, float]],
    when: datetime,
    *,
    weights: dict[str, float] | None = None,
    spacing_m: float | None = None,
    distance_m: float = 0.0,
    duration_s: float = 0.0,
    polyline: str = "",
) -> ScoredRoute:
    """Score a full route from its decoded polyline."""
    from .config import get_settings

    weights = validate_weights(weights or DEFAULT_WEIGHTS)
    spacing = spacing_m or get_settings().segment_length_m
    layers = get_layers()
    grid = get_grid()

    sampled = resample(points, spacing)
    segments = [
        score_segment(a, b, when, weights, layers, grid)
        for a, b in zip(sampled, sampled[1:], strict=False)
    ]

    if not segments:
        return ScoredRoute(
            segments=[],
            css=0.0,
            band=band_for(0.0),
            distance_m=distance_m,
            duration_s=duration_s,
            polyline=polyline,
        )

    total_len = sum(s.length_m for s in segments) or 1.0

    # Length-weighted, so a long unlit stretch counts for more than a brief one.
    css = sum(s.css * s.length_m for s in segments) / total_len

    layer_contributions = {
        name: sum(s.contributions[name] * s.length_m for s in segments) / total_len
        for name in weights
    }

    covered = sum(
        s.length_m
        for s in segments
        if any(layer.has(s.cell_id) for layer in layers.as_dict().values())
    )

    return ScoredRoute(
        segments=segments,
        css=css,
        band=band_for(css),
        distance_m=distance_m or total_len,
        duration_s=duration_s,
        polyline=polyline,
        layer_contributions=layer_contributions,
        worst_stretch=find_worst_stretch(segments),
        coverage=covered / total_len,
    )


def find_worst_stretch(segments: list[ScoredSegment], min_length_m: float = 150.0) -> dict | None:
    """The longest contiguous run of below-par segments.

    Reported instead of the single worst segment because one bad 100 m sample
    is usually a data artefact, while 600 m of continuous amber is a road. This
    is the thing a route explanation should actually be about.

    ``reason`` is the layer to blame, and it is **None** when no layer with data
    can be blamed. The stretch itself is still real — those segments genuinely
    score below the route average — but with the layers that explain it unbuilt,
    naming one would be inventing a cause. Callers must handle the None rather
    than assuming there is always something to point at.
    """
    if not segments:
        return None

    threshold = sum(s.css for s in segments) / len(segments)
    best: dict | None = None
    run: list[ScoredSegment] = []

    def close(run: list[ScoredSegment]) -> None:
        nonlocal best
        if not run:
            return
        length = sum(s.length_m for s in run)
        if length < min_length_m:
            return
        mean_css = sum(s.css * s.length_m for s in run) / length
        if best is None or length > best["length_m"]:
            # Attribute the stretch to the layer that lost it the most points
            # overall, not the one that dominates any single segment — and only
            # among layers holding data for the cells in question, or an unbuilt
            # layer's NEUTRAL deficit wins every time by default.
            totals: dict[str, float] = {}
            for s in run:
                for name, d in s.grounded_deficits.items():
                    totals[name] = totals.get(name, 0.0) + d * s.length_m
            best = {
                "length_m": length,
                "css": mean_css,
                "band": band_for(mean_css),
                "reason": max(totals, key=lambda k: totals[k]) if totals else None,
                "start": run[0].start,
                "end": run[-1].end,
            }

    for seg in segments:
        if seg.css < threshold:
            run.append(seg)
        else:
            close(run)
            run = []
    close(run)
    return best


def rank_routes(routes: list[ScoredRoute]) -> list[ScoredRoute]:
    """Safest first. Distance breaks ties.

    The tiebreak matters more than it looks: with sparse layers many routes
    score identically, and without it the "safest" route would be whichever one
    Google happened to list first — a stable-looking answer with nothing behind
    it.
    """
    return sorted(routes, key=lambda r: (-r.css, r.distance_m))
