from __future__ import annotations

import pytest

from app.config import DEFAULT_WEIGHTS, validate_weights
from app.data.base import NEUTRAL, normalise, severity_of
from app.scoring import ScoredRoute, find_worst_stretch, rank_routes, resample, score_segment

# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------


def test_default_weights_are_valid():
    assert validate_weights(DEFAULT_WEIGHTS) == DEFAULT_WEIGHTS


def test_weights_must_sum_to_one():
    bad = dict(DEFAULT_WEIGHTS)
    bad["cip"] += 0.1
    with pytest.raises(ValueError, match="sum to 1.0"):
        validate_weights(bad)


def test_weights_must_cover_every_layer():
    partial = {k: v for k, v in DEFAULT_WEIGHTS.items() if k != "nsi"}
    with pytest.raises(ValueError, match="missing layers"):
        validate_weights(partial)


# ---------------------------------------------------------------------------
# Normalisation — the sign convention is the thing most likely to silently break
# ---------------------------------------------------------------------------


def test_normalise_inverts_when_asked():
    raw = {"a": 0.0, "b": 50.0, "c": 100.0}

    plain = normalise(raw, percentile_clip=1.0)
    assert plain["a"] < plain["c"]

    inverted = normalise(raw, invert=True, percentile_clip=1.0)
    assert inverted["a"] > inverted["c"]
    assert inverted["a"] == pytest.approx(1.0)
    assert inverted["c"] == pytest.approx(0.0)


def test_normalise_output_is_always_in_range():
    raw = {f"c{i}": float(i**3) for i in range(200)}
    for v in normalise(raw, invert=True).values():
        assert 0.0 <= v <= 1.0


def test_percentile_clip_stops_one_outlier_flattening_everything():
    # A normal spread plus one cell a thousand times the rest — the heavy tail
    # the clip exists for (Connaught Place against a residential lane).
    raw = {f"c{i}": float(i) for i in range(1, 101)}
    raw["outlier"] = 100_000.0

    clipped = normalise(raw, percentile_clip=0.95)
    unclipped = normalise(raw, percentile_clip=1.0)

    # Unclipped, a mid-range cell is indistinguishable from the lowest one.
    assert unclipped["c50"] < 0.01
    # Clipped, it sits where it belongs — mid-range.
    assert 0.3 < clipped["c50"] < 0.7
    assert clipped["outlier"] == 1.0


def test_normalise_falls_back_to_neutral_on_featureless_data():
    """If every cell holds the same value the layer has no spatial signal, and
    saying so beats confidently scoring the whole city 0.0."""
    flat = normalise({f"c{i}": 7.0 for i in range(50)})
    assert set(flat.values()) == {0.5}


def test_normalise_still_ranks_outliers_when_the_clip_lands_flat():
    # 100 identical cells plus one outlier: the percentile lands inside the flat
    # region, so the clip has to fall back to the true max or the outlier and
    # the ordinary cells become indistinguishable.
    raw = {f"c{i}": 1.0 for i in range(100)}
    raw["outlier"] = 1000.0

    out = normalise(raw, percentile_clip=0.95)
    assert out["outlier"] > out["c1"]


def test_severity_lookup_is_forgiving_about_labels():
    assert severity_of("Sexual Assault") == severity_of("sexual_assault")
    assert severity_of("robbery") > severity_of("vehicle_theft")
    assert severity_of(None) == severity_of("other")
    assert severity_of("some offence we have never seen") == severity_of("other")


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


def test_resample_gives_roughly_even_spacing():
    from app.grid import haversine_m

    # A ~2 km straight line with deliberately uneven vertices.
    points = [(28.60, 77.20), (28.605, 77.20), (28.62, 77.20)]
    out = resample(points, 100.0)

    gaps = [haversine_m(*a, *b) for a, b in zip(out, out[1:], strict=False)]
    # Ignore the final partial gap, which is whatever is left over.
    assert all(80 < g < 120 for g in gaps[:-1])
    assert len(out) > 15


def test_resample_preserves_endpoints():
    points = [(28.60, 77.20), (28.62, 77.22)]
    out = resample(points, 100.0)
    assert out[0] == points[0]
    assert out[-1] == points[-1]


def test_resample_handles_degenerate_input():
    assert resample([], 100.0) == []
    assert resample([(28.6, 77.2)], 100.0) == [(28.6, 77.2)]
    assert resample([(28.6, 77.2), (28.6, 77.2)], 100.0) == [(28.6, 77.2)]


# ---------------------------------------------------------------------------
# Segment scoring
# ---------------------------------------------------------------------------


def test_missing_data_scores_neutral(grid, empty_layers, night):
    seg = score_segment(
        (28.6139, 77.2090), (28.6145, 77.2095), night, DEFAULT_WEIGHTS, empty_layers, grid
    )
    # Four layers return NEUTRAL with no data. NSI is the deliberate exception:
    # it only ever penalises, so no news means 1.0, not 0.5 (see data/nsi.py).
    # That lifts an otherwise-blank score to 0.5 + 0.5*w_nsi.
    expected = NEUTRAL + NEUTRAL * DEFAULT_WEIGHTS["nsi"]
    assert seg.css == pytest.approx(expected, abs=0.01)
    assert all(v == NEUTRAL for k, v in seg.raw.items() if k != "nsi")


def test_good_cell_outscores_bad_cell(grid, layers, night):
    good = score_segment(
        (28.6139, 77.2090), (28.6140, 77.2091), night, DEFAULT_WEIGHTS, layers, grid
    )
    bad = score_segment(
        (28.6100, 77.2200), (28.6101, 77.2201), night, DEFAULT_WEIGHTS, layers, grid
    )
    assert good.css > bad.css
    assert good.band == "safe"
    assert bad.band == "risk"


def test_contributions_reconstruct_the_score(grid, layers, night):
    seg = score_segment(
        (28.6139, 77.2090), (28.6140, 77.2091), night, DEFAULT_WEIGHTS, layers, grid
    )
    assert sum(seg.contributions.values()) == pytest.approx(seg.css)
    # Contribution and deficit together account for the layer's whole weight.
    for name, weight in DEFAULT_WEIGHTS.items():
        assert seg.contributions[name] + seg.deficits[name] == pytest.approx(weight)


def test_dominant_deficit_identifies_the_actual_problem(grid, layers, night):
    # The bad cell is worst on lighting (0.1) and crime (0.1), but lighting
    # carries the heavier weight, so it should take the blame.
    seg = score_segment(
        (28.6100, 77.2200), (28.6101, 77.2201), night, DEFAULT_WEIGHTS, layers, grid
    )
    assert seg.dominant_deficit in {"ntls", "cip"}
    assert seg.deficits[seg.dominant_deficit] == max(seg.deficits.values())


def test_lighting_is_ignored_in_daylight(grid, layers, midday, night):
    at_night = score_segment(
        (28.6100, 77.2200), (28.6101, 77.2201), night, DEFAULT_WEIGHTS, layers, grid
    )
    at_noon = score_segment(
        (28.6100, 77.2200), (28.6101, 77.2201), midday, DEFAULT_WEIGHTS, layers, grid
    )
    assert at_noon.raw["ntls"] == 1.0
    assert at_night.raw["ntls"] < 0.5
    assert at_noon.css > at_night.css


# ---------------------------------------------------------------------------
# Route aggregation
# ---------------------------------------------------------------------------


def _route(css: float, distance: float) -> ScoredRoute:
    from app.config import band_for

    return ScoredRoute(
        segments=[], css=css, band=band_for(css), distance_m=distance,
        duration_s=distance / 1.35, polyline="",
    )


def test_ranking_prefers_safety_then_distance():
    routes = [_route(0.5, 1000), _route(0.8, 3000), _route(0.8, 2000)]
    ranked = rank_routes(routes)

    assert ranked[0].css == 0.8
    assert ranked[0].distance_m == 2000  # shorter of the two equally safe ones
    assert ranked[-1].css == 0.5


def test_worst_stretch_ignores_a_single_bad_sample(grid, layers, night):
    segs = [
        score_segment((28.6139, 77.2090), (28.6140, 77.2091), night, DEFAULT_WEIGHTS, layers, grid)
        for _ in range(10)
    ]
    # One isolated bad segment, well under the 150 m minimum run length.
    segs[5] = score_segment(
        (28.6100, 77.2200), (28.6101, 77.2201), night, DEFAULT_WEIGHTS, layers, grid
    )
    assert find_worst_stretch(segs) is None


def test_worst_stretch_finds_a_sustained_run(grid, layers, night):
    good = [
        score_segment((28.6139, 77.2090), (28.6148, 77.2090), night, DEFAULT_WEIGHTS, layers, grid)
        for _ in range(5)
    ]
    bad = [
        score_segment((28.6100, 77.2200), (28.6109, 77.2200), night, DEFAULT_WEIGHTS, layers, grid)
        for _ in range(5)
    ]
    worst = find_worst_stretch(good + bad + good)

    assert worst is not None
    assert worst["length_m"] > 150
    assert worst["band"] in {"caution", "risk"}
    assert worst["reason"] in DEFAULT_WEIGHTS


def test_worst_stretch_of_empty_route_is_none():
    assert find_worst_stretch([]) is None
