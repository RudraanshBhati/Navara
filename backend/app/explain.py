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

from .config import LAYER_FLAGS, LAYER_LABELS, STREET_TERM_CONFIDENCE
from .data import LayerRegistry, get_layers
from .data.ntls import DAY_END_HOUR, DAY_START_HOUR
from .scoring import SAME_SCORE, ScoredRoute, ScoredSegment

#: How a mode reads in a sentence.
MODE_PHRASES = {
    "walk": ("on foot", "walk"),
    "two_wheeler": ("by two-wheeler", "ride"),
    "drive": ("by car", "drive"),
}

#: A stretch with nothing open is worth a bullet of its own past this length.
GAP_REASON_M = 500.0

#: Same for a stretch with no homes or mapped places.
ISOLATED_REASON_M = 500.0

#: Worth saying when the nearest open help is farther than a few minutes' run.
HELP_REASON_M = 1000.0

#: A layer has to actually be losing the segment points before we blame it.
#: Without a floor, every safe segment still has a "largest" deficit and the
#: map fills up with flags on roads that are completely fine.
DEFICIT_FLOOR = 0.06

#: A layer must have measured at least this much of a route before it may be
#: named in a route-level claim. A layer covering a tenth of the route may well
#: be right about that tenth, but "below par along most of the route" is a claim
#: about the whole thing.
MIN_LAYER_COVERAGE = 0.5


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

    # Cell layers are checked against the registry; street-level terms (road
    # type, open places) are not in it, and the segment records whether their
    # data existed when it was scored.
    layers = (registry or get_layers()).as_dict()

    def measured(name: str) -> bool:
        if name in layers:
            return layers[name].has(segment.cell_id)
        return name in segment.grounded

    grounded = {
        name: deficit
        for name, deficit in segment.deficits.items()
        if measured(name) and deficit >= DEFICIT_FLOOR
    }
    if not grounded:
        return None
    return LAYER_FLAGS[max(grounded, key=lambda k: grounded[k])]


def annotate_segments(route: ScoredRoute, registry: LayerRegistry | None = None) -> list[dict]:
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
        # `reason` is None when no layer with data could be blamed. The stretch
        # is still worth reporting — it really does score below the route
        # average — but the sentence stops at what is known.
        if worst["reason"]:
            label = LAYER_LABELS[worst["reason"]].lower()
            reasons.append(
                f"{_distance(worst['length_m'])} of this route scores below its own "
                f"average, mostly on {label}."
            )
        else:
            reasons.append(
                f"{_distance(worst['length_m'])} of this route scores below its own "
                "average, but the layers that would explain why have no data here."
            )

    # A long run with nothing open is reported in its own right: it is the
    # case a count of places hides (all the shops at one end, then nothing).
    gap = route.longest_gap
    gap_reported = bool(gap and gap["length_m"] >= GAP_REASON_M)
    if gap_reported:
        reasons.append(
            f"{_distance(gap['length_m'])} of this route passes no mapped place likely "
            "to be open at this hour."
        )

    # After dark, how alone you would be and how far help is. In daylight these
    # are the same roads with people on them and most places open.
    night = not (DAY_START_HOUR <= when.hour < DAY_END_HOUR)
    isolated = route.longest_isolated
    if night and isolated and isolated["length_m"] >= ISOLATED_REASON_M:
        reasons.append(
            f"{_distance(isolated['length_m'])} of this route has no detected homes and no "
            "mapped place nearby."
        )
    help_point = route.farthest_help
    if night and help_point and help_point["distance_m"] >= HELP_REASON_M:
        reasons.append(
            f"At its farthest, this route is {_distance(help_point['distance_m'])} in a "
            "straight line from a mapped police station, hospital or petrol pump likely "
            "to be open."
        )

    # Which terms are pulling the route down overall, as a share of what they
    # could have contributed — restricted to terms that actually measured this
    # route. An unbuilt layer contributes half its weight from NEUTRAL, which
    # reads as a 50% shortfall and produces a confident "X is below par along
    # most of the route" sourced from nothing at all.
    weights = route.weights
    shortfalls = {
        name: (weights[name] - got) / weights[name]
        for name, got in route.layer_contributions.items()
        if weights.get(name) and _layer_coverage(route, name) >= MIN_LAYER_COVERAGE
    }
    layer_map = layers.as_dict()
    # The gap bullet already says nothing is open; a second "open places is
    # below par" bullet would be the same fact twice.
    if gap_reported:
        shortfalls.pop("act", None)
    for name, shortfall in sorted(shortfalls.items(), key=lambda kv: -kv[1])[:2]:
        if shortfall < 0.25:
            continue
        confidence = (
            layer_map[name].confidence if name in layer_map else STREET_TERM_CONFIDENCE[name]
        )
        text = f"{LAYER_LABELS[name]} is below par along most of the route."
        coverage = _layer_coverage(route, name)
        if confidence == "low":
            text += " This layer is a rough proxy — treat it as a hint, not a fact."
        if coverage < 0.9:
            text += f" Measured on {coverage:.0%} of the route."
        reasons.append(text)

    if route.coverage < 0.6:
        reasons.append(
            f"Only {route.coverage:.0%} of this route falls in cells we have data "
            "for, so the score is less certain than usual."
        )

    # Say when the score is time-sensitive. The same route can flip bands between
    # afternoon and midnight, and a user who does not know that will reasonably
    # assume the rating is a property of the road.
    if night:
        verb = MODE_PHRASES.get(route.mode, MODE_PHRASES["walk"])[1]
        reasons.append(
            f"Scored for a night-time {verb} — lighting, what is open and how busy "
            "the streets are all count here, and all would be different in daylight."
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
    how = MODE_PHRASES.get(route.mode, MODE_PHRASES["walk"])[0]
    return (
        f"{_distance(route.distance_m)}, about {_duration(route.duration_s)} {how}. "
        f"This route {band_text}."
    )


def fastest_and_safest(routes: list[ScoredRoute]) -> tuple[ScoredRoute, ScoredRoute]:
    """The quickest route, and the highest-scoring one (ties to the quicker)."""
    fastest = min(routes, key=lambda r: (r.duration_s, -r.css))
    safest = min(routes, key=lambda r: (-r.css, r.duration_s))
    return fastest, safest


def tradeoff(fastest: ScoredRoute, safest: ScoredRoute) -> str:
    """What choosing the safest route over the fastest one costs and buys.

    Every figure is a difference between two measured routes. Facts are left
    out when the data behind them is not built, and when they do not favour
    the safer route — this sentence argues nothing, it reports.
    """
    if fastest is safest:
        return "The fastest route is also the safest one for this trip."

    d_css = safest.css - fastest.css
    if d_css < 0.02:
        return (
            f"The fastest route scores about the same on safety ({fastest.css:.2f} against "
            f"{safest.css:.2f}), so the slower one buys little."
        )

    extra = _duration(max(safest.duration_s - fastest.duration_s, 0))
    facts: list[str] = []
    more_open = len(safest.open_places) - len(fastest.open_places)
    if more_open > 0:
        facts.append(f"passes {more_open} more place{'s' if more_open != 1 else ''} likely open")
    if safest.main_road_share is not None and fastest.main_road_share is not None:
        d_main = safest.main_road_share - fastest.main_road_share
        if d_main >= 0.05:
            facts.append(f"spends {d_main:.0%} more of the way on main roads")
    if safest.longest_gap and fastest.longest_gap:
        d_gap = fastest.longest_gap["length_m"] - safest.longest_gap["length_m"]
        if d_gap >= 200:
            facts.append(f"has a {_distance(d_gap)} shorter stretch with nothing open")
    # Cameras are left out on purpose. Their mapping is too uneven for "more
    # cameras on this route" to mean more than "someone mapped this area".
    d_isolated = (fastest.longest_isolated or {}).get("length_m", 0.0) - (
        safest.longest_isolated or {}
    ).get("length_m", 0.0)
    if d_isolated >= 200:
        facts.append(f"has {_distance(d_isolated)} less isolated road")
    if safest.farthest_help and fastest.farthest_help:
        d_help = fastest.farthest_help["distance_m"] - safest.farthest_help["distance_m"]
        if d_help >= 200:
            facts.append(f"stays {_distance(d_help)} closer to open help")

    text = f"The safest route takes {extra} longer than the fastest"
    if facts:
        text += ", " + (", ".join(facts[:-1]) + " and " + facts[-1] if len(facts) > 1 else facts[0])
    return f"{text}. Safety score {safest.css:.2f} against {fastest.css:.2f}."


def route_facts(route: ScoredRoute) -> list[dict]:
    """What a route card shows: {key, value, label, text}, where text is the full line.

    Counts, not judgements — the judgement is the band. Each is left out when
    the data behind it is not built, rather than shown as zero.
    """
    out: list[dict] = []

    def add(key: str, value: str, label: str, text: str) -> None:
        out.append({"key": key, "value": value, "label": label, "text": text})

    if route.longest_gap is not None or route.open_places:
        n = len(route.open_places)
        add(
            "open",
            str(n),
            "places likely open",
            f"Passes {n} mapped place{'s' if n != 1 else ''} likely open at this hour",
        )
    if route.main_road_share is not None:
        share = f"{route.main_road_share:.0%}"
        add("main_road", share, "on main roads", f"{share} on main roads")
    gap = route.longest_gap
    if gap is not None:
        d = _distance(gap["length_m"])
        add("gap", d, "with nothing open", f"Longest stretch with nothing open: {d}")
    isolated = route.longest_isolated
    if isolated is not None:
        d = _distance(isolated["length_m"])
        add(
            "isolated",
            d,
            "isolated stretch",
            f"Longest stretch with no detected homes or mapped places: {d}",
        )
    help_point = route.farthest_help
    if help_point is not None:
        d = _distance(help_point["distance_m"])
        add(
            "help",
            d,
            "farthest from open help",
            f"Farthest from an open police station, hospital or petrol pump: {d}",
        )
    # Signals and cameras are only ever counts of what is mapped: no entry at
    # all when there are none, because zero mapped is not zero on the street.
    n = len(route.signals)
    if n:
        s = "s" if n != 1 else ""
        add("signals", str(n), f"traffic signal{s}", f"About {n} mapped traffic signal{s} on the route")
    n = len(route.cameras)
    if n:
        s = "s" if n != 1 else ""
        add("cameras", str(n), f"mapped CCTV camera{s}", f"{n} mapped CCTV camera{s} near the route")
    return out


def route_highlights(route: ScoredRoute) -> list[str]:
    """The route facts as plain lines."""
    return [fact["text"] for fact in route_facts(route)]


def departure_note(options: list[dict]) -> str:
    """What leaving at another of these times would change, if anything."""
    current = next(o for o in options if o["current"])
    best = max(options, key=lambda o: o["css"])
    if best["css"] - current["css"] < SAME_SCORE:
        return "Leaving at any of these other times would not score noticeably better."
    return (
        f"Leaving at {_clock(best['at'])} scores {best['css']:.2f}, against "
        f"{current['css']:.2f} at {_clock(current['at'])}."
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
    worst = alternative.worst_stretch
    if worst and worst["reason"]:
        reason = (
            f", avoiding {_distance(worst['length_m'])} of "
            f"{LAYER_LABELS[worst['reason']].lower()} on the alternative"
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


def _layer_coverage(route: ScoredRoute, name: str) -> float:
    """Fraction of route length where this layer actually had data."""
    total = sum(s.length_m for s in route.segments)
    if total <= 0:
        return 0.0
    return sum(s.length_m for s in route.segments if name in s.grounded) / total


def _distance(metres: float) -> str:
    if metres < 950:
        return f"{round(metres / 50) * 50:.0f} m"
    return f"{metres / 1000:.1f} km"


def _clock(when: datetime) -> str:
    return f"{when.hour % 12 or 12}:{when.minute:02d} {'AM' if when.hour < 12 else 'PM'}"


def _duration(seconds: float) -> str:
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, rem = divmod(minutes, 60)
    return f"{hours}h {rem:02d}m"
