"""Street-level terms: opening hours, road type, open places, dark gaps, modes."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import app.features as features_mod
import app.pois as pois_mod
from app.config import DELHI_TZ, MODE_WEIGHTS, validate_weights
from app.explain import departure_note, route_facts, route_highlights, route_reasons
from app.features import (
    MINOR_ROAD_VALUE,
    RoadIndex,
    is_open,
    parse_open,
    reset_features,
    road_value,
)
from app.grid import Grid
from app.main import app
from app.pois import POI, POIIndex
from app.scoring import departure_options, score_at, score_polyline

# Mon 20 Oct 2026, Delhi time
MON_10AM = datetime(2026, 10, 19, 10, 0, tzinfo=DELHI_TZ)
MON_11PM = datetime(2026, 10, 19, 23, 0, tzinfo=DELHI_TZ)
TUE_1AM = datetime(2026, 10, 20, 1, 0, tzinfo=DELHI_TZ)
SUN_NOON = datetime(2026, 10, 25, 12, 0, tzinfo=DELHI_TZ)


# -- opening_hours ---------------------------------------------------------


@pytest.mark.parametrize(
    "spec, when, expected",
    [
        ("24/7", MON_11PM, True),
        ("Mo-Sa 10:00-21:00", MON_10AM, True),
        ("Mo-Sa 10:00-21:00", MON_11PM, False),
        ("Mo-Sa 10:00-21:00; Su off", SUN_NOON, False),
        ("09:00-13:00,14:00-20:00", MON_10AM, True),
        ("Mo-Su 18:00-02:00", MON_11PM, True),
        ("Mo-Su 18:00-02:00", TUE_1AM, True),  # yesterday's late range
        ("Mo 18:00-02:00", TUE_1AM, True),
        ("Tu 18:00-02:00", TUE_1AM, False),
    ],
)
def test_opening_hours(spec, when, expected):
    assert parse_open(spec, when) is expected


@pytest.mark.parametrize(
    "spec", ["", "sunrise-sunset", "PH off", "Jan-Mar 10:00-18:00", "by appointment"]
)
def test_unparseable_hours_defer_to_the_category(spec):
    assert parse_open(spec, MON_10AM) is None


def test_category_defaults_when_hours_are_unknown():
    police = POI("p", 0, 0, "police", "PS")
    shop = POI("s", 0, 0, "shop", "Kirana")
    clinic = POI("c", 0, 0, "hospital", "Clinic", {"amenity": "clinic"})
    hospital = POI("h", 0, 0, "hospital", "AIIMS", {"amenity": "hospital"})

    assert is_open(police, TUE_1AM)
    assert not is_open(shop, TUE_1AM)
    assert not is_open(clinic, TUE_1AM)
    assert is_open(hospital, TUE_1AM)


# -- road type -------------------------------------------------------------


def test_road_index_finds_the_road_under_a_point():
    idx = RoadIndex([{"cls": "primary", "pts": [[28.6000, 77.2000], [28.6000, 77.2100]]}])

    assert idx.road_class(28.6001, 77.2050) == "primary"  # ~11 m off the centreline
    assert idx.road_class(28.6010, 77.2050) is None  # ~110 m away: a side lane


def test_bigger_road_wins_where_two_meet():
    idx = RoadIndex(
        [
            {"cls": "tertiary", "pts": [[28.6, 77.20], [28.6, 77.21]]},
            {"cls": "trunk", "pts": [[28.59, 77.205], [28.61, 77.205]]},
        ]
    )
    assert idx.road_class(28.6, 77.205) == "trunk"


def test_road_value_depends_on_mode():
    # A highway is a good place for a car and a poor one to walk along.
    assert road_value("motorway", "drive") > road_value("motorway", "walk")
    assert road_value(None, "drive") == MINOR_ROAD_VALUE


def test_every_mode_has_valid_weights():
    for mode, weights in MODE_WEIGHTS.items():
        assert validate_weights(weights) == weights, mode
    assert MODE_WEIGHTS["drive"]["road"] > MODE_WEIGHTS["walk"]["road"]
    assert MODE_WEIGHTS["walk"]["act"] >= MODE_WEIGHTS["drive"]["act"]


# -- scoring with street data ----------------------------------------------

# An east-west line of ~1 km, sampled every 100 m.
ROUTE = [(28.6000, 77.2000 + i * 0.001) for i in range(11)]


@pytest.fixture
def street_data(tmp_path, monkeypatch):
    """A main road under the first half of ROUTE; three late-night places
    clustered at its start, then nothing."""
    roads = tmp_path / "roads.json"
    roads.write_text(
        json.dumps({"roads": [{"cls": "primary", "pts": [[28.6, 77.2], [28.6, 77.205]]}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(features_mod, "ROADS_PATH", roads)
    monkeypatch.setattr(
        pois_mod,
        "_index",
        POIIndex(
            [
                POI("a", 28.6002, 77.2005, "police", "PS"),
                POI("b", 28.6002, 77.2007, "food", "Dhaba", {"opening_hours": "24/7"}),
                POI("c", 28.6002, 77.2009, "shop", "Kirana"),  # closed at 11pm
            ]
        ),
    )
    reset_features()


def test_route_counts_open_places_and_finds_the_dark_gap(street_data):
    route = score_polyline(ROUTE, MON_11PM, mode="walk")

    assert {p.id for p in route.open_places} == {"a", "b"}  # the shop is shut
    assert route.longest_gap is not None
    assert route.longest_gap["length_m"] > 600
    assert route.main_road_share == pytest.approx(0.5, abs=0.15)


def test_a_minor_road_counts_against_a_car_more_than_a_walker(street_data):
    walk = score_polyline(ROUTE, MON_11PM, mode="walk")
    drive = score_polyline(ROUTE, MON_11PM, mode="drive")

    def road_loss(r):
        return r.weights["road"] - r.layer_contributions["road"]

    assert road_loss(drive) > road_loss(walk)


def test_an_unmapped_street_is_unknown_not_empty(street_data):
    """Past the cluster of places, nothing is mapped within 200 m: the
    open-places term must not count that as a measured zero."""
    route = score_polyline(ROUTE, MON_11PM)
    far = route.segments[-1]

    assert "act" not in far.grounded
    assert "act" in route.segments[0].grounded


def test_no_street_data_means_no_claims():
    route = score_polyline(ROUTE, MON_11PM)
    assert route.longest_gap is None
    assert route.main_road_share is None
    assert all("act" not in s.grounded and "road" not in s.grounded for s in route.segments)


# -- API --------------------------------------------------------------------

CP = {"lat": 28.6139, "lng": 77.2090}
SAKET = {"lat": 28.5355, "lng": 77.2410}


@pytest.fixture
def mock_routing(monkeypatch):
    import app.config as config

    monkeypatch.setattr(config.get_settings(), "google_maps_api_key", "", raising=False)


@pytest.mark.parametrize(
    "mode, phrase", [("walk", "on foot"), ("two_wheeler", "by two-wheeler"), ("drive", "by car")]
)
def test_each_travel_mode_routes_and_says_how(mock_routing, mode, phrase):
    body = (
        TestClient(app)
        .post("/routes", json={"origin": CP, "destination": SAKET, "travel_mode": mode})
        .json()
    )
    assert body["travel_mode"] == mode
    assert body["weights"] == MODE_WEIGHTS[mode]
    assert phrase in body["routes"][0]["summary"]


def test_vehicles_are_faster_than_walking(mock_routing):
    client = TestClient(app)

    def duration(mode):
        r = client.post("/routes", json={"origin": CP, "destination": SAKET, "travel_mode": mode})
        return r.json()["routes"][0]["duration_s"]

    assert duration("two_wheeler") < duration("walk")
    assert duration("drive") < duration("walk")


def test_unknown_mode_is_rejected(mock_routing):
    r = TestClient(app).post(
        "/routes", json={"origin": CP, "destination": SAKET, "travel_mode": "boat"}
    )
    assert r.status_code == 422


# -- cameras, isolation, help, time to leave ---------------------------------


@pytest.fixture
def safety_data(tmp_path, monkeypatch):
    """Places only at ROUTE's start, one camera beside it and one far off, and
    residents only in the first cell."""
    grid = Grid()
    cameras = tmp_path / "cameras.json"
    cameras.write_text(
        json.dumps({"cameras": [[28.6002, 77.2050], [28.70, 77.30]]}), encoding="utf-8"
    )
    population = tmp_path / "population.json"
    population.write_text(
        json.dumps({"cells": {grid.cell_id(28.6, 77.2005): 5000.0}}), encoding="utf-8"
    )
    monkeypatch.setattr(features_mod, "CAMERAS_PATH", cameras)
    monkeypatch.setattr(features_mod, "POPULATION_PATH", population)
    monkeypatch.setattr(
        pois_mod,
        "_index",
        POIIndex(
            [
                POI("ps", 28.6002, 77.2000, "police", "PS"),
                POI("clinic", 28.6002, 77.2100, "hospital", "Clinic", {"amenity": "clinic"}),
                POI("pump", 28.6002, 77.2100, "fuel", "Pump", {"opening_hours": "Mo-Su 06:00-22:00"}),
            ]
        ),
    )
    reset_features()


def test_only_cameras_near_the_route_are_counted(safety_data):
    assert score_polyline(ROUTE, MON_11PM).cameras == [(28.6002, 77.2050)]


def test_no_mapped_camera_makes_no_claim(street_data):
    route = score_polyline(ROUTE, MON_11PM)
    assert route.cameras == []
    assert not any("CCTV" in h for h in route_highlights(route))


def test_cameras_do_not_change_the_score(safety_data, tmp_path, monkeypatch):
    with_cameras = score_polyline(ROUTE, MON_11PM).css
    monkeypatch.setattr(features_mod, "CAMERAS_PATH", tmp_path / "gone.json")
    reset_features()
    assert score_polyline(ROUTE, MON_11PM).css == with_cameras


def test_empty_land_with_nothing_mapped_is_isolated(safety_data):
    route = score_polyline(ROUTE, MON_11PM)

    assert route.segments[0].isolated is False
    assert route.segments[5].isolated is True
    assert route.longest_isolated is not None
    assert route.longest_isolated["length_m"] > 300


def test_isolation_is_unknown_without_population_data(street_data):
    route = score_polyline(ROUTE, MON_11PM)
    assert route.longest_isolated is None
    assert all(s.isolated is None for s in route.segments)


def test_isolation_is_unknown_outside_the_grid(safety_data):
    far_south = [(28.30 + i * 0.001, 77.20) for i in range(11)]
    assert score_polyline(far_south, MON_11PM).longest_isolated is None


def test_help_distance_ignores_clinics_and_closed_places(safety_data):
    """At 11pm the pump is shut and a clinic never counts, so the police
    station at the far end of the line is the only help there is."""
    found = score_polyline(ROUTE, MON_11PM).farthest_help

    assert found["place"].id == "ps"
    assert found["distance_m"] == pytest.approx(950, abs=60)


def test_a_place_that_opens_later_counts_in_the_day(safety_data):
    found = score_polyline(ROUTE, MON_11PM.replace(hour=12)).farthest_help
    assert found["distance_m"] < 600


def test_isolation_is_a_reason_at_night_only(safety_data):
    night = route_reasons(score_polyline(ROUTE, MON_11PM), MON_11PM)
    assert any("no detected homes" in r for r in night)

    day = MON_11PM.replace(hour=12)
    assert not any("no detected homes" in r for r in route_reasons(score_polyline(ROUTE, day), day))


def test_rescoring_at_the_same_time_reproduces_the_score(street_data):
    route = score_polyline(ROUTE, MON_11PM)
    assert score_at(route, MON_11PM) == pytest.approx(route.css)


def test_rescoring_for_another_hour_matches_a_fresh_score(street_data):
    route = score_polyline(ROUTE, MON_11PM)
    noon = MON_11PM.replace(hour=12)
    assert score_at(route, noon) == pytest.approx(score_polyline(ROUTE, noon).css)


def test_departure_options_drop_the_past_and_mark_the_request(street_data):
    route = score_polyline(ROUTE, MON_11PM)
    options = departure_options(route, MON_11PM, now=MON_11PM)

    assert [o["at"] for o in options] == sorted(o["at"] for o in options)
    assert all(o["at"] >= MON_11PM for o in options)
    assert [o["current"] for o in options].count(True) == 1
    assert next(o for o in options if o["current"])["css"] == route.css


def test_departure_note_names_a_better_time_only_if_it_is_better():
    at = MON_11PM.replace(hour=23)
    same = [{"at": at, "css": 0.6, "current": True}, {"at": at, "css": 0.61, "current": False}]
    assert "would not score noticeably better" in departure_note(same)

    better = [
        {"at": at.replace(hour=20), "css": 0.72, "current": False},
        {"at": at, "css": 0.6, "current": True},
    ]
    assert departure_note(better) == "Leaving at 8:00 PM scores 0.72, against 0.60 at 11:00 PM."


# -- traffic signals ---------------------------------------------------------


@pytest.fixture
def signal_data(tmp_path, monkeypatch):
    """A four-node junction on ROUTE, another 500 m along, and one off the route."""
    signals = tmp_path / "signals.json"
    signals.write_text(
        json.dumps(
            {
                "signals": [
                    [28.6000, 77.2020],
                    [28.6001, 77.2021],
                    [28.5999, 77.2021],
                    [28.6000, 77.2023],
                    [28.6000, 77.2070],
                    [28.6100, 77.2070],
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(features_mod, "SIGNALS_PATH", signals)
    reset_features()


def test_signal_nodes_at_one_junction_count_once(signal_data):
    route = score_polyline(ROUTE, MON_11PM, mode="two_wheeler")
    assert len(route.signals) == 2


def test_a_signal_off_the_route_is_not_counted(signal_data):
    route = score_polyline(ROUTE, MON_11PM, mode="drive")
    assert all(lat < 28.605 for lat, _ in route.signals)


def test_signals_are_not_reported_on_foot(signal_data):
    assert score_polyline(ROUTE, MON_11PM, mode="walk").signals == []


def test_signals_do_not_change_the_score(signal_data):
    with_signals = score_polyline(ROUTE, MON_11PM, mode="drive")
    assert with_signals.signals
    features_mod.get_features().signals = features_mod.np.empty((0, 2))
    assert score_polyline(ROUTE, MON_11PM, mode="drive").css == with_signals.css


def test_no_mapped_signal_makes_no_claim(street_data):
    route = score_polyline(ROUTE, MON_11PM, mode="drive")
    assert not any(f["key"] == "signals" for f in route_facts(route))


def test_every_fact_has_a_value_label_and_sentence(signal_data, safety_data):
    facts = route_facts(score_polyline(ROUTE, MON_11PM, mode="drive"))
    assert facts
    assert all(f["value"] and f["label"] and f["text"] for f in facts)
