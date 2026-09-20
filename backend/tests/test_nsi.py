"""The news layer's decay and blur logic — the parts that are easy to get
subtly wrong and impossible to eyeball on a map."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.data.nsi import Incident, NSILayer, _spatial_falloff
from app.grid import get_grid


def _incident(days_ago: float, lat=28.6139, lon=77.2090, severity=0.9, confidence=1.0) -> Incident:
    return Incident(
        id=f"i{days_ago}{lat}",
        lat=lat,
        lon=lon,
        occurred_at=(datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        severity=severity,
        category="assault",
        headline="Test",
        url="https://example.test/x",
        source="fixture",
        confidence=confidence,
    )


def _layer(incidents: list[Incident]) -> NSILayer:
    layer = NSILayer()
    layer.incidents = incidents
    layer._loaded = True
    layer._build_index()
    return layer


def test_no_news_means_a_clean_score():
    """Absence of news must not penalise. This layer only ever subtracts."""
    layer = _layer([])
    assert layer.value("r0000c0000", datetime.now(UTC)) == 1.0


def test_a_recent_incident_lowers_the_score():
    now = datetime.now(UTC)
    layer = _layer([_incident(0.5)])
    cell = get_grid().cell_id(28.6139, 77.2090)

    assert layer.value(cell, now) < 1.0


def test_signal_decays_with_age():
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)

    fresh = _layer([_incident(0.1)]).value(cell, now)
    week_old = _layer([_incident(7)]).value(cell, now)
    month_old = _layer([_incident(29)]).value(cell, now)

    assert fresh < week_old < month_old <= 1.0


def test_halflife_actually_halves():
    from app.config import get_settings

    now = datetime.now(UTC)
    halflife = get_settings().news_halflife_days
    cell = get_grid().cell_id(28.6139, 77.2090)

    fresh_pressure = _layer([_incident(0.0)]).pressure(cell, now)
    aged_pressure = _layer([_incident(halflife)]).pressure(cell, now)

    assert aged_pressure == pytest.approx(fresh_pressure / 2, rel=0.05)


def test_incidents_past_max_age_are_ignored_entirely():
    from app.config import get_settings

    now = datetime.now(UTC)
    too_old = get_settings().news_max_age_days + 5
    cell = get_grid().cell_id(28.6139, 77.2090)

    assert _layer([_incident(too_old)]).value(cell, now) == 1.0


def test_an_incident_dated_a_moment_ahead_still_counts():
    """Clock skew must not silently erase the freshest incidents.

    An incident geocoded "just now" routinely carries a timestamp a few
    milliseconds ahead of the clock it is later scored against. Dropping it as
    future-dated would zero out exactly the incidents this layer exists to
    surface — and it fails silently, as a score that looks merely clean.
    """
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)
    just_ahead = _incident(-1.0 / 86_400.0)  # one second into the future

    assert _layer([just_ahead]).value(cell, now) < 1.0


def test_a_genuinely_future_dated_incident_is_dropped():
    """The tolerance is for clock skew, not for a bad extraction."""
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)

    assert _layer([_incident(-3.0)]).value(cell, now) == 1.0


def test_low_confidence_incidents_move_the_score_less():
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)

    confident = _layer([_incident(1, confidence=1.0)]).value(cell, now)
    hedged = _layer([_incident(1, confidence=0.3)]).value(cell, now)

    assert hedged > confident


def test_severity_scales_the_penalty():
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)

    severe = _layer([_incident(1, severity=1.0)]).value(cell, now)
    minor = _layer([_incident(1, severity=0.2)]).value(cell, now)

    assert minor > severe


def test_incidents_accumulate():
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)

    one = _layer([_incident(1)]).value(cell, now)
    three = _layer([_incident(1), _incident(1.1), _incident(1.2)]).value(cell, now)

    assert three < one


def test_score_never_leaves_the_unit_interval():
    """A pile-up of severe incidents must saturate, not go negative."""
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)

    layer = _layer([_incident(0.1 * i, severity=1.0) for i in range(30)])
    assert 0.0 <= layer.value(cell, now) <= 1.0


def test_effect_spreads_to_neighbouring_cells_and_fades():
    """A news location is a neighbourhood, not a GPS fix — so must the penalty be."""
    now = datetime.now(UTC)
    grid = get_grid()
    layer = _layer([_incident(0.5)])

    centre = grid.cell_id(28.6139, 77.2090)
    row, col = grid.decode(centre)

    at_centre = layer.value(centre, now)
    one_over = layer.value(grid.encode(row, col + 1), now)
    far_away = layer.value(grid.encode(row, col + 20), now)

    assert at_centre < one_over < 1.0
    assert far_away == 1.0


def test_incidents_outside_delhi_are_dropped_not_misplaced():
    # A geocode that escaped to Mumbai must not land anywhere on the Delhi grid.
    layer = _layer([_incident(1, lat=19.0760, lon=72.8777)])
    assert layer.coverage == 0


def test_spatial_falloff_is_monotonic_and_bounded():
    assert _spatial_falloff(0, 750) == pytest.approx(1.0)
    assert _spatial_falloff(750, 750) == 0.0
    assert _spatial_falloff(1000, 750) == 0.0

    values = [_spatial_falloff(d, 750) for d in range(0, 800, 50)]
    assert all(a >= b for a, b in zip(values, values[1:], strict=False))


def test_active_incidents_are_returned_newest_first():
    now = datetime.now(UTC)
    cell = get_grid().cell_id(28.6139, 77.2090)
    layer = _layer([_incident(5), _incident(1), _incident(3)])

    active = layer.active_incidents(cell, now)
    assert len(active) == 3
    assert active[0].occurred_dt > active[1].occurred_dt > active[2].occurred_dt


def test_persistence_roundtrip(processed_dir, monkeypatch):
    import app.data.nsi as nsi_mod

    monkeypatch.setattr(nsi_mod, "INCIDENTS_PATH", processed_dir / "nsi_incidents.json")

    original = [_incident(1), _incident(2)]
    NSILayer().save_incidents(original, meta={"test": True})

    reloaded = NSILayer().load()
    assert len(reloaded.incidents) == 2
    assert {i.id for i in reloaded.incidents} == {i.id for i in original}
    assert reloaded.available
