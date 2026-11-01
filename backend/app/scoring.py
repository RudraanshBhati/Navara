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
from datetime import datetime, timedelta

from .config import DEFAULT_WEIGHTS, MODE_WEIGHTS, STREET_TERMS, band_for, validate_weights
from .data import NEUTRAL, LayerRegistry, get_layers
from .features import (
    MAIN_CLASSES,
    activity_value,
    get_features,
    is_open,
    place_weight,
    road_value,
)
from .grid import Grid, get_grid, haversine_m, interpolate
from .pois import POI

#: A stretch with nothing open must be at least this long to be reported. One
#: 100 m sample between two shops is not a gap anyone would notice.
MIN_GAP_M = 200.0

#: Nobody notices one empty block. An isolated stretch must last this long.
MIN_ISOLATED_M = 300.0

#: Hours either side of the requested time offered as other times to leave.
DEPARTURE_HOURS = (-3, -2, -1, 1, 2, 3, 4)


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
    #: Street-level facts behind the `road` and `act` terms.
    road_class: str | None = None
    open_places: tuple[POI, ...] = ()
    #: Everything near enough to count, open or not; `act` at another hour.
    nearby_places: tuple[POI, ...] = ()
    #: No detected homes and no mapped place. None where that is unknown.
    isolated: bool | None = None

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
    mode: str = "walk"
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    #: Distinct places likely open along the route at departure time.
    open_places: list[POI] = field(default_factory=list)
    #: Fraction of route length on a main road (motorway to secondary), or None
    #: when the road data is not built.
    main_road_share: float | None = None
    #: The longest run with no mapped place open: {length_m, start, end}, or None.
    longest_gap: dict | None = None
    #: The longest run with no detected homes and no mapped place, or None.
    longest_isolated: dict | None = None
    #: {distance_m, point, place}: where this route is farthest from open help.
    farthest_help: dict | None = None
    #: Mapped outdoor cameras near the route. Empty says nothing about the street.
    cameras: list[tuple[float, float]] = field(default_factory=list)
    #: One point per mapped signalled junction. Riders and drivers only.
    signals: list[tuple[float, float]] = field(default_factory=list)


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
    street: dict[str, tuple[float, bool]] | None = None,
    *,
    road_class: str | None = None,
    open_places: tuple[POI, ...] = (),
    nearby_places: tuple[POI, ...] = (),
) -> ScoredSegment:
    """Score one segment at its midpoint.

    Midpoint rather than start point: a 100 m segment can straddle a cell
    boundary, and sampling at the start systematically attributes the segment
    to whichever cell it is leaving rather than the one it mostly runs through.

    `street` carries the per-segment terms as name -> (value, grounded). A
    street term with no entry scores NEUTRAL and is ungrounded, like an
    unbuilt layer.
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
        if name in STREET_TERMS:
            v, has = (street or {}).get(name, (NEUTRAL, False))
        else:
            layer = layer_map[name]
            v, has = layer.value(cell_id, when), layer.has(cell_id)
        raw[name] = v
        contributions[name] = weight * v
        deficits[name] = weight * (1.0 - v)
        if has:
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
        road_class=road_class,
        open_places=open_places,
        nearby_places=nearby_places,
    )


def score_polyline(
    points: list[tuple[float, float]],
    when: datetime,
    *,
    weights: dict[str, float] | None = None,
    mode: str = "walk",
    spacing_m: float | None = None,
    distance_m: float = 0.0,
    duration_s: float = 0.0,
    polyline: str = "",
) -> ScoredRoute:
    """Score a full route from its decoded polyline, for one travel mode."""
    from .config import get_settings

    weights = validate_weights(weights or MODE_WEIGHTS.get(mode, DEFAULT_WEIGHTS))
    spacing = spacing_m or get_settings().segment_length_m
    layers = get_layers()
    grid = get_grid()
    features = get_features()

    sampled = resample(points, spacing)
    segments = []
    for a, b in zip(sampled, sampled[1:], strict=False):
        f = features.at((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, when)
        act = activity_value(sum(place_weight(p) for p in f.open_places))
        street = {
            "road": (road_value(f.road_class, mode), f.road_grounded),
            # Unmeasured scores NEUTRAL, like an unbuilt layer, not a zero.
            "act": (act if f.activity_grounded else NEUTRAL, f.activity_grounded),
        }
        segment = score_segment(
            a,
            b,
            when,
            weights,
            layers,
            grid,
            street,
            road_class=f.road_class,
            open_places=tuple(f.open_places),
            nearby_places=tuple(f.nearby_places),
        )
        empty = features.is_empty_land(segment.cell_id)
        if empty is not None and features.pois.available:
            segment.isolated = empty and not f.activity_grounded
        segments.append(segment)

    if not segments:
        return ScoredRoute(
            segments=[],
            css=0.0,
            band=band_for(0.0),
            distance_m=distance_m,
            duration_s=duration_s,
            polyline=polyline,
            mode=mode,
            weights=weights,
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

    midpoints = [s.midpoint for s in segments]
    # A person on foot does not have to stop for a signal the way a vehicle
    # does. Starts as well as midpoints: at 100 m spacing, midpoints alone are
    # too far apart to catch a node within 30 m of the line.
    signals = (
        features.signals_on([p for s in segments for p in (s.start, s.midpoint)])
        if mode != "walk"
        else []
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
        mode=mode,
        weights=weights,
        open_places=_distinct_places(segments),
        main_road_share=(
            sum(s.length_m for s in segments if s.road_class in MAIN_CLASSES) / total_len
            if features.roads is not None
            else None
        ),
        longest_gap=find_longest_gap(segments) if features.pois.available else None,
        longest_isolated=find_longest_isolated(segments),
        farthest_help=features.farthest_from_help(midpoints, when),
        cameras=features.cameras_near(midpoints),
        signals=signals,
    )


def _distinct_places(segments: list[ScoredSegment]) -> list[POI]:
    seen: dict[str, POI] = {}
    for s in segments:
        for p in s.open_places:
            seen.setdefault(p.id, p)
    return list(seen.values())


def find_longest_gap(segments: list[ScoredSegment], min_length_m: float = MIN_GAP_M) -> dict | None:
    """The longest contiguous stretch with no mapped place open nearby.

    Counting places alone hides the case that matters at night: twenty shops
    bunched at one end of a route and a kilometre of nothing after them score
    the same as twenty spread evenly. This is the measure of the nothing.
    """
    return _longest_run(segments, lambda s: not s.open_places, min_length_m)


def find_longest_isolated(
    segments: list[ScoredSegment], min_length_m: float = MIN_ISOLATED_M
) -> dict | None:
    """The longest stretch with no detected homes and no mapped place nearby.

    Ridge forest, parkland, floodplain and open land, measured rather than
    guessed from a road's name.
    """
    return _longest_run(segments, lambda s: s.isolated is True, min_length_m)


def _longest_run(segments, matches, min_length_m: float) -> dict | None:
    best: dict | None = None
    run: list[ScoredSegment] = []
    for seg in [*segments, None]:
        if seg is not None and matches(seg):
            run.append(seg)
            continue
        length = sum(s.length_m for s in run)
        if run and length >= min_length_m and (best is None or length > best["length_m"]):
            best = {"length_m": length, "start": run[0].start, "end": run[-1].end}
        run = []
    return best


def score_at(route: ScoredRoute, when: datetime, layers: LayerRegistry | None = None) -> float:
    """This route's score had it been requested for `when`.

    The geometry and everything found near it stay fixed; only what depends on
    the clock is read again, which is why this needs no new routing call.
    """
    layer_map = (layers or get_layers()).as_dict()
    total = weighted = 0.0
    for s in route.segments:
        css = 0.0
        for name, weight in route.weights.items():
            if name == "act" and name in s.grounded:
                v = activity_value(sum(place_weight(p) for p in s.nearby_places if is_open(p, when)))
            elif name in STREET_TERMS:
                v = s.raw[name]
            else:
                v = layer_map[name].value(s.cell_id, when)
            css += weight * v
        total += s.length_m
        weighted += css * s.length_m
    return weighted / total if total else 0.0


def departure_options(route: ScoredRoute, when: datetime, now: datetime) -> list[dict]:
    """The same route scored at nearby departure times, earliest first.

    Other times fall on the hour. Past times are dropped: nobody can leave then.
    """
    hour = when.replace(minute=0, second=0, microsecond=0)
    times = [hour + timedelta(hours=h) for h in DEPARTURE_HOURS]
    options = [{"at": when, "css": route.css, "current": True}]
    options += [{"at": t, "css": score_at(route, t), "current": False} for t in times if t >= now]
    options.sort(key=lambda o: o["at"])
    for option in options:
        option["band"] = band_for(option["css"])
    return options


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


#: Scores closer than this are a tie. The model is not precise to the second
#: decimal, and explain.compare already says so in words.
SAME_SCORE = 0.02


def rank_routes(routes: list[ScoredRoute]) -> list[ScoredRoute]:
    """Recommended first, then the rest safest-first.

    The recommendation is the shortest of the routes scoring within SAME_SCORE
    of the best. Ranking strictly by score once recommended a two-wheeler route
    3.7 km longer for a 0.008 edge — while the comparison sentence, which
    treats that as a tie, told the user it was "simply the shorter one".

    Distance breaks exact ties among the rest: with sparse layers many routes
    score identically, and without it the order would be whichever Google
    happened to list first.
    """
    if not routes:
        return []
    best = max(r.css for r in routes)
    tied = [r for r in routes if best - r.css < SAME_SCORE]
    pick = min(tied, key=lambda r: (r.distance_m, -r.css))
    rest = sorted((r for r in routes if r is not pick), key=lambda r: (-r.css, r.distance_m))
    return [pick, *rest]
