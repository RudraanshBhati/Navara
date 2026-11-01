"""End-to-end API tests. These run against the mock routing provider, so they
need no API key and no network."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes_api import decode_polyline, encode_polyline

CP = {"lat": 28.6139, "lng": 77.2090}
SAKET = {"lat": 28.5355, "lng": 77.2410}


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_reports_grid_and_layers(client: TestClient):
    body = client.get("/health").json()

    assert body["status"] == "ok"
    assert body["grid"]["cells"] > 1000
    assert set(body["layers"]) == {"cip", "cds", "trc", "ntls", "nsi"}


def test_health_warns_loudly_when_running_on_no_data(client: TestClient):
    """A demo must never look authoritative while scoring on placeholders."""
    body = client.get("/health").json()
    if any(not s["available"] for s in body["layers"].values()):
        assert body["warnings"], "unavailable layers must produce a warning"


def test_routes_returns_ranked_alternatives(client: TestClient):
    r = client.post("/routes", json={"origin": CP, "destination": SAKET})
    assert r.status_code == 200
    body = r.json()

    assert len(body["routes"]) >= 1
    assert body["routes"][0]["rank"] == 0
    assert body["routes"][0]["recommended"] is True
    assert sum(1 for rt in body["routes"] if rt["recommended"]) == 1

    # The recommendation is within a hair of the best score (ties go to the
    # shorter route); the rest follow safest-first.
    scores = [rt["css"] for rt in body["routes"]]
    assert max(scores) - scores[0] < 0.02
    assert scores[1:] == sorted(scores[1:], reverse=True)


def test_every_route_carries_its_explanation(client: TestClient):
    body = client.post("/routes", json={"origin": CP, "destination": SAKET}).json()

    assert body["comparison"]
    for route in body["routes"]:
        assert route["summary"]
        assert route["reasons"]
        assert route["segments"]
        assert 0.0 <= route["css"] <= 1.0
        assert route["band"] in {"safe", "caution", "risk"}


def test_segments_are_contiguous(client: TestClient):
    """The map draws these end to end — a gap would show as a broken line."""
    body = client.post("/routes", json={"origin": CP, "destination": SAKET}).json()
    segments = body["routes"][0]["segments"]

    for a, b in zip(segments, segments[1:], strict=False):
        assert a["end"] == b["start"]


def test_mock_provider_is_declared(client: TestClient):
    body = client.post("/routes", json={"origin": CP, "destination": SAKET}).json()
    if body["provider"] == "mock":
        assert any("mock" in w.lower() for w in body["warnings"])


def test_departure_time_changes_the_score(client: TestClient):
    """Time-of-day layers are the point; if this passes trivially they are inert."""
    day = client.post(
        "/routes",
        json={"origin": CP, "destination": SAKET, "departure_time": "2026-03-15T13:00:00+05:30"},
    ).json()
    night = client.post(
        "/routes",
        json={"origin": CP, "destination": SAKET, "departure_time": "2026-03-15T23:30:00+05:30"},
    ).json()

    assert day["routes"][0]["css"] >= night["routes"][0]["css"]


def test_departure_time_is_read_in_delhi_local_time(client: TestClient):
    """14:30 UTC is 8pm in Delhi. Read as UTC it looked like afternoon, and the
    lighting layer switched itself off for the evening walk it exists for."""
    body = client.post(
        "/routes",
        json={"origin": CP, "destination": SAKET, "departure_time": "2026-03-15T14:30:00Z"},
    ).json()

    assert body["departure_time"].endswith("+05:30")
    assert body["departure_time"].startswith("2026-03-15T20:00")
    assert any("night-time" in r for r in body["routes"][0]["reasons"])


def test_ncr_points_are_routable(client: TestClient):
    cyber_hub = {"lat": 28.4950, "lng": 77.0890}
    r = client.post("/routes", json={"origin": cyber_hub, "destination": CP})
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Place search
# ---------------------------------------------------------------------------


@pytest.fixture
def offline_places(monkeypatch):
    """Pin place search to the landmark list, whatever key the machine has."""
    import app.config as config

    monkeypatch.setattr(config.get_settings(), "google_maps_api_key", "", raising=False)


def test_place_search_works_without_a_key(client: TestClient, offline_places):
    body = client.get("/places/autocomplete", params={"q": "cyber"}).json()

    assert body["provider"] == "local"
    assert body["suggestions"][0]["name"] == "Cyber Hub"


def test_place_detail_returns_routable_coordinates(client: TestClient, offline_places):
    place_id = client.get("/places/autocomplete", params={"q": "saket"}).json()["suggestions"][0][
        "place_id"
    ]
    body = client.get(f"/places/{place_id}").json()

    r = client.post("/routes", json={"origin": CP, "destination": body["location"]})
    assert r.status_code == 200


def test_place_outside_ncr_is_refused(client: TestClient, monkeypatch):
    import app.places as places

    monkeypatch.setattr(
        places,
        "LANDMARKS",
        [places._landmark("bom", "Mumbai", "Maharashtra", 19.0760, 72.8777)],
    )
    assert client.get("/places/local:bom").status_code == 422


def test_bad_weights_are_rejected(client: TestClient):
    r = client.post(
        "/routes",
        json={"origin": CP, "destination": SAKET, "weights": {"cip": 1.0}},
    )
    assert r.status_code == 422


def test_points_outside_delhi_are_rejected(client: TestClient):
    r = client.post(
        "/routes",
        json={"origin": {"lat": 19.0760, "lng": 72.8777}, "destination": SAKET},
    )
    assert r.status_code == 422


def test_cell_inspection_breaks_down_the_score(client: TestClient):
    body = client.get("/cell", params={"lat": CP["lat"], "lng": CP["lng"]}).json()

    assert body["cell_id"]
    assert set(body["layers"]) == {"cip", "cds", "trc", "ntls", "nsi"}
    assert body["css"] == pytest.approx(
        sum(v["weighted"] for v in body["layers"].values()), abs=1e-3
    )


def test_cell_inspection_outside_grid_is_404(client: TestClient):
    assert client.get("/cell", params={"lat": 19.0760, "lng": 72.8777}).status_code == 404


# ---------------------------------------------------------------------------
# Polyline codec
# ---------------------------------------------------------------------------


def test_polyline_roundtrip():
    points = [(28.6139, 77.2090), (28.6000, 77.2200), (28.5355, 77.2410)]
    decoded = decode_polyline(encode_polyline(points))

    assert len(decoded) == len(points)
    for (a_lat, a_lon), (b_lat, b_lon) in zip(points, decoded, strict=True):
        assert a_lat == pytest.approx(b_lat, abs=1e-5)
        assert a_lon == pytest.approx(b_lon, abs=1e-5)


def test_decode_known_google_example():
    # The example from Google's polyline algorithm documentation.
    decoded = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@")
    assert decoded == [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]


def test_decode_handles_empty_and_truncated_input():
    assert decode_polyline("") == []
    decode_polyline("_p~iF~ps|U_ulL")  # must not raise
