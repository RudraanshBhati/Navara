from __future__ import annotations

from app.config import DEFAULT_WEIGHTS, band_for
from app.explain import _distance, _duration, compare, segment_flag, summarise
from app.scoring import ScoredRoute, ScoredSegment, score_segment


def _segment(css: float, worst_layer: str = "ntls") -> ScoredSegment:
    deficits = {k: 0.01 for k in DEFAULT_WEIGHTS}
    deficits[worst_layer] = 0.20
    return ScoredSegment(
        start=(28.61, 77.20),
        end=(28.62, 77.21),
        length_m=100.0,
        cell_id="r0001c0001",
        css=css,
        band=band_for(css),
        contributions={k: v * css for k, v in DEFAULT_WEIGHTS.items()},
        deficits=deficits,
        raw=dict.fromkeys(DEFAULT_WEIGHTS, css),
    )


def _route(css: float, distance: float, worst: dict | None = None) -> ScoredRoute:
    return ScoredRoute(
        segments=[], css=css, band=band_for(css), distance_m=distance,
        duration_s=distance / 1.35, polyline="", worst_stretch=worst,
    )


def _grounded_segment(grid, css: float, worst_layer: str = "ntls"):
    """A segment sitting in a cell the `layers` fixture actually has data for."""
    seg = _segment(css, worst_layer)
    seg.cell_id = grid.cell_id(28.6100, 77.2200)
    return seg


def test_safe_segments_are_not_flagged(grid, layers):
    """Flagging everything is the same as flagging nothing."""
    assert segment_flag(_grounded_segment(grid, 0.85), layers) is None


def test_unsafe_segment_is_flagged_with_its_dominant_layer(grid, layers):
    flag = segment_flag(_grounded_segment(grid, 0.30, worst_layer="ntls"), layers)
    assert flag is not None
    assert "lit" in flag.lower()


def test_a_marginal_deficit_does_not_earn_a_flag(grid, layers):
    seg = _grounded_segment(grid, 0.35)
    seg.deficits = dict.fromkeys(DEFAULT_WEIGHTS, 0.01)
    assert segment_flag(seg, layers) is None


def test_a_layer_with_no_data_may_not_blame_a_segment(grid, empty_layers):
    """An unbuilt layer sits at NEUTRAL, which leaves a deficit big enough to
    clear the floor. Without a data check that stamps every road in Delhi with
    a confident accusation sourced from nothing."""
    seg = _grounded_segment(grid, 0.30, worst_layer="cip")
    assert segment_flag(seg, empty_layers) is None


def test_comparison_says_so_when_safety_is_a_wash():
    """The build plan's key case: not saying anything reads like the model
    never ran."""
    text = compare(_route(0.70, 2000), _route(0.705, 2500))
    assert "same" in text.lower()


def test_comparison_quantifies_a_real_detour():
    chosen = _route(0.80, 2500)
    alternative = _route(0.55, 2000, worst={"length_m": 600, "reason": "ntls"})

    text = compare(chosen, alternative)
    assert "500 m longer" in text
    assert "lighting" in text.lower()
    assert "0.80" in text and "0.55" in text


def test_comparison_handles_a_strictly_better_route():
    text = compare(_route(0.80, 1800), _route(0.55, 2400))
    assert "shorter and safer" in text


def test_comparison_with_no_alternative():
    assert "only route" in compare(_route(0.8, 1000), None)


def test_summary_mentions_distance_and_verdict():
    text = summarise(_route(0.25, 3000))
    assert "3.0 km" in text
    assert "avoid" in text.lower()


def test_reasons_flag_thin_data_coverage(grid, layers, night):
    from app.explain import route_reasons

    route = _route(0.5, 1000)
    route.layer_contributions = dict(DEFAULT_WEIGHTS)
    route.coverage = 0.2

    reasons = route_reasons(route, night)
    assert any("data" in r for r in reasons)


def test_reasons_caveat_the_low_confidence_layer(grid, layers, night):
    """CDS is a proxy; explanations built on it must say so."""
    from app.explain import route_reasons

    route = _route(0.5, 1000)
    route.layer_contributions = {**DEFAULT_WEIGHTS, "cds": 0.01}
    route.coverage = 1.0

    reasons = route_reasons(route, night)
    assert any("proxy" in r.lower() for r in reasons)


def test_reasons_are_never_empty(grid, layers, night):
    from app.explain import route_reasons

    route = _route(0.9, 1000)
    route.layer_contributions = dict(DEFAULT_WEIGHTS)
    route.coverage = 1.0

    assert route_reasons(route, night)


def test_annotate_segments_matches_the_map_contract(grid, layers, night):
    from app.explain import annotate_segments

    segs = [
        score_segment((28.6139, 77.2090), (28.6148, 77.2090), night, DEFAULT_WEIGHTS, layers, grid)
    ]
    out = annotate_segments(_route_with(segs))

    assert out[0]["start"] == {"lat": 28.6139, "lng": 77.2090}
    assert out[0]["band"] in {"safe", "caution", "risk"}


def _route_with(segments):
    route = _route(0.5, 1000)
    route.segments = segments
    return route


def test_distance_formatting():
    assert _distance(400) == "400 m"
    assert _distance(1500) == "1.5 km"
    assert _distance(12345) == "12.3 km"


def test_duration_formatting():
    assert _duration(600) == "10 min"
    assert _duration(3900) == "1h 05m"
