"""Crowd layer: how the inputs combine, and what absence means for each."""

from __future__ import annotations

from datetime import datetime

import pytest

from app.data.cds import HOURLY_ACTIVITY, CDSLayer, combine_presence


def test_mapping_a_shop_never_makes_a_street_look_emptier():
    """An average got this wrong: a faint POI signal diluted a strong population one."""
    population = {"a": 0.6, "b": 0.6}
    out = combine_presence(["a", "b"], population=population, poi={"a": 0.02})

    for h in range(24):
        assert out["a"][h] >= out["b"][h]


def test_sources_add_up_but_never_past_one():
    out = combine_presence(["c"], population={"c": 0.9}, poi={"c": 0.9})["c"]
    assert out[12] > 0.9
    assert out[12] <= 1.0


def test_no_population_is_a_measurement():
    """The settlement layer is complete: no people there means no people."""
    out = combine_presence(["empty", "busy"], population={"busy": 1.0})
    assert out["empty"] == [0.0] * 24
    assert max(out["busy"]) == pytest.approx(1.0)


def test_residents_fade_less_at_night_than_shops():
    pop_only = combine_presence(["c"], population={"c": 1.0})["c"]
    poi_only = combine_presence(["c"], poi={"c": 1.0})["c"]

    assert pop_only[3] > poi_only[3]  # 3am
    assert pop_only[3] == pytest.approx(0.5 + 0.5 * HOURLY_ACTIVITY[3])


def test_transit_is_read_by_the_hour():
    service = [0.0] * 24
    service[22] = 1.0
    out = combine_presence(["s"], transit={"s": service})["s"]

    assert out[22] == pytest.approx(1.0)
    assert out[2] == 0.0


def test_a_cell_with_no_inputs_is_left_unscored():
    """Only POIs available, and this cell has none: no basis for a score."""
    assert combine_presence(["x"], poi={}) == {}


def test_hourly_values_round_trip_and_take_precedence(processed_dir):
    layer = CDSLayer()
    hours = [round(h / 23, 4) for h in range(24)]
    layer.save_hourly({"r0001c0001": hours})

    loaded = CDSLayer().load()
    assert loaded.value("r0001c0001", datetime(2026, 1, 1, 23)) == hours[23]
    assert loaded.has("r0001c0001")
    assert loaded.value("r0009c0009", datetime(2026, 1, 1, 23)) == 0.5  # unscored -> neutral
