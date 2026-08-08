"""Turning scores into sentences a person can act on.

The rule this module follows: never assert more than the data supports. Every
sentence here is generated from a term in the CSS sum, and where a layer is
weak (CDS) or the route crosses cells with no data at all, the wording says so
rather than projecting confidence the model has not earned.

The one place we cite external fact is the news layer, and there we quote the
headline and link the article rather than restating it as our own claim.
"""

from __future__ import annotations

from datetime import datetime

from .config import LAYER_FLAGS, LAYER_LABELS
from .data import LayerRegistry, get_layers
from .data.ntls import DAY_END_HOUR, DAY_START_HOUR
from .scoring import ScoredRoute, ScoredSegment

#: A layer has to actually be losing the segment points before we blame it.
#: Without a floor, every safe segment still has a "largest" deficit and the
#: map fills up with flags on roads that are completely fine.
DEFICIT_FLOOR = 0.06


def segment_flag(segment: ScoredSegment, registry: LayerRegistry | None = None) -> str | None:
    """The one-phrase reason this segment is not green, or None if it is fine.

    A layer may only be blamed for a segment it actually has data for. Without
    that check, an unbuilt layer sits at NEUTRAL (0.5), which leaves a deficit
    of half its weight — enough to clear the floor and stamp every road in the
    city with a confident "elevated reported crime in this area" on the strength
    of no data whatsoever. The placeholder is supposed to be silent, not
    accusatory.
    """
    if segment.band == "safe":
        return None

    layers = (registry or get_layers()).as_dict()
    grounded = {
        name: deficit
        for name, deficit in segment.deficits.items()
        if layers[name].has(segment.cell_id) and deficit >= DEFICIT_FLOOR
    }
    if not grounded:
        return None
    return LAYER_FLAGS[max(grounded, key=lambda k: grounded[k])]


def annotate_segments(
    route: ScoredRoute, registry: LayerRegistry | None = None
) -> list[dict]:
    """Segment payload for the map overlay."""
    registry = registry or get_layers()
    return [
        {
            "start": {"lat": s.start[0], "lng": s.start[1]},
            "end": {"lat": s.end[0], "lng": s.end[1]},
            "css": round(s.css, 4),
            "band": s.band,
            "flag": segment_flag(s, registry),
            "cell_id": s.cell_id,
        }
        for s in route.segments
    ]


def route_reasons(route: ScoredRoute, when: datetime) -> list[str]:
    """Bullet points for the why-panel, strongest first."""
    reasons: list[str] = []
    layers = get_layers()

    worst = route.worst_stretch
    if worst:
        label = LAYER_LABELS[worst["reason"]].lower()
        reasons.append(
            f"{_distance(worst['length_m'])} of this route scores below its own "
            f"average, mostly on {label}."
        )

    # Which layers are pulling the route down overall, as a share of what they
    # could have contributed.
    from .config import DEFAULT_WEIGHTS

    shortfalls = {
        name: (DEFAULT_WEIGHTS[name] - got) / DEFAULT_WEIGHTS[name]
        for name, got in route.layer_contributions.items()
        if DEFAULT_WEIGHTS.get(name)
    }
    for name, shortfall in sorted(shortfalls.items(), key=lambda kv: -kv[1])[:2]:
        if shortfall < 0.25:
            continue
        layer = layers.as_dict()[name]
        text = f"{LAYER_LABELS[name]} is below par along most of the route."
        if layer.confidence == "low":
            text += " This layer is a rough proxy — treat it as a hint, not a fact."
        reasons.append(text)

    if route.coverage < 0.6:
        reasons.append(
            f"Only {route.coverage:.0%} of this route falls in cells we have data "
            "for, so the score is less certain than usual."
        )

    # Say when the score is time-sensitive. The same route can flip bands between
    # afternoon and midnight, and a user who does not know that will reasonably
    # assume the rating is a property of the road.
    if not (DAY_START_HOUR <= when.hour < DAY_END_HOUR):
        reasons.append(
            "Scored for a night-time walk — lighting and how busy the streets are "
            "both count here, and both would be different in daylight."
        )

    if not reasons:
        reasons.append("No stretch of this route scores badly on any layer.")

    return reasons


def news_citations(route: ScoredRoute, when: datetime, limit: int = 3) -> list[dict]:
    """Recent incidents near this route, with links.

    The news layer is the only one that can point at a specific event, so it is
    the only one allowed to make a specific claim — and it has to show its
    source when it does.
    """
    nsi = get_layers().nsi
    if not nsi.available:
        return []

    seen: set[str] = set()
    out: list[dict] = []
    for seg in route.segments:
        for inc in nsi.active_incidents(seg.cell_id, when, limit=limit):
            if inc.id in seen:
                continue
            seen.add(inc.id)
            out.append(
                {
                    "headline": inc.headline,
                    "url": inc.url,
                    "source": inc.source,
                    "occurred_at": inc.occurred_at,
                    "category": inc.category,
                    "location": inc.location_text,
                    "confidence": inc.confidence,
                }
            )
    out.sort(key=lambda d: d["occurred_at"], reverse=True)
    return out[:limit]


def summarise(route: ScoredRoute) -> str:
    """One line describing the route on its own terms."""
    band_text = {
        "safe": "scores well across its length",
        "caution": "is mostly fine with some stretches to watch",
        "risk": "has significant stretches we would avoid",
    }[route.band]
    return (
        f"{_distance(route.distance_m)}, about {_duration(route.duration_s)} on foot. "
        f"This route {band_text}."
    )


def compare(chosen: ScoredRoute, alternative: ScoredRoute | None) -> str:
    """The sentence the whole product hangs on: why this one and not that one.

    The build plan calls out the case that matters most — when the safest route
    *is* the fastest route. Saying nothing there reads like the safety model did
    not run at all, so we say so explicitly.
    """
    if alternative is None:
        return "This was the only route available for this trip."

    d_dist = chosen.distance_m - alternative.distance_m
    d_css = chosen.css - alternative.css

    if abs(d_css) < 0.02:
        return (
            "The alternatives score about the same on safety, so this is simply "
            f"the shorter one ({_distance(chosen.distance_m)})."
        )

    reason = ""
    if alternative.worst_stretch:
        reason = (
            f", avoiding {_distance(alternative.worst_stretch['length_m'])} of "
            f"{LAYER_LABELS[alternative.worst_stretch['reason']].lower()} on the alternative"
        )

    if d_dist <= 0:
        return (
            f"This route is both shorter and safer than the alternative{reason}."
            if d_dist < 0
            else f"Same distance as the alternative, but safer{reason}."
        )

    return (
        f"{_distance(d_dist)} longer than the fastest route{reason}. "
        f"Safety score {chosen.css:.2f} against {alternative.css:.2f}."
    )


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _distance(metres: float) -> str:
    if metres < 950:
        return f"{round(metres / 50) * 50:.0f} m"
    return f"{metres / 1000:.1f} km"


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rem = divmod(minutes, 60)
    return f"{hours}h {rem:02d}m"
