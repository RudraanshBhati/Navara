"""Map places: categorising OSM tags, filling a view, and typo-tolerant search."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app.pois as pois_mod
from app.main import app
from app.pois import POI, POIIndex, classify

CP = (28.6315, 77.2167)


def _poi(id_, cat, name="", lat=CP[0], lng=CP[1], **tags):
    return POI(id=id_, lat=lat, lng=lng, category=cat, name=name, tags=tags)


@pytest.fixture
def index(monkeypatch) -> POIIndex:
    idx = POIIndex(
        [
            _poi("n1", "police", "Connaught Place Police Station"),
            _poi("n2", "hospital", "Dr Ram Manohar Lohia Hospital", lat=28.626, lng=77.201),
            _poi("n3", "bus", ""),
            _poi("n4", "shop", "Hauz Khas Village Books", lat=28.553, lng=77.194),
            *[_poi(f"s{i}", "food", f"Cafe {i}") for i in range(30)],
        ]
    )
    monkeypatch.setattr(pois_mod, "_index", idx)
    return idx


def test_classify_covers_what_the_map_shows():
    assert classify({"amenity": "police"}) == "police"
    assert classify({"amenity": "clinic"}) == "hospital"
    assert classify({"railway": "station", "station": "subway"}) == "metro"
    assert classify({"railway": "station", "network": "Delhi Metro"}) == "metro"
    assert classify({"railway": "station", "name": "New Delhi"}) == "train"
    assert classify({"highway": "bus_stop"}) == "bus"
    assert classify({"shop": "chemist"}) == "pharmacy"
    assert classify({"shop": "clothes"}) == "shop"
    assert classify({"leisure": "park"}) is None


def test_a_crowded_view_keeps_police_and_hospitals(index):
    found, truncated = index.in_bbox(77.19, 28.55, 77.25, 28.64, limit=5)

    assert truncated
    cats = [p.category for p in found]
    assert cats[:2] == ["police", "hospital"]
    assert len(found) == 5


def test_category_filter(index):
    found, _ = index.in_bbox(77.19, 28.55, 77.25, 28.64, categories={"bus"})
    assert [p.id for p in found] == ["n3"]


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("connaught", "Connaught Place Police Station"),
        ("conaught place police", "Connaught Place Police Station"),
        ("ram manohar lohiya", "Dr Ram Manohar Lohia Hospital"),
        ("hauz khaz", "Hauz Khas Village Books"),
    ],
)
def test_search_tolerates_typos(index, typed, expected):
    assert index.search(typed)[0].name == expected


def test_search_ignores_unrelated_names(index):
    assert index.search("qwertyuiop") == []


def test_pois_endpoint(index):
    body = (
        TestClient(app).get("/pois", params={"bbox": "77.19,28.55,77.25,28.64", "limit": 3}).json()
    )

    assert body["truncated"] is True
    assert body["pois"][0]["category"] == "police"
    assert body["categories"]["bus"] == "Bus stop"


def test_pois_endpoint_refuses_a_whole_country_view(index):
    r = TestClient(app).get("/pois", params={"bbox": "68,8,97,37"})
    assert r.status_code == 422


def test_offline_search_reaches_osm_places_with_typos(index, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config.get_settings(), "google_maps_api_key", "", raising=False)
    client = TestClient(app)

    body = client.get(
        "/places/autocomplete", params={"q": "ram manohar lohiya", "lat": CP[0], "lng": CP[1]}
    ).json()
    hit = next(s for s in body["suggestions"] if "Lohia" in s["name"])
    assert hit["kind"] == "hospital"
    assert hit["distance_m"] > 0

    detail = client.get(f"/places/{hit['place_id']}").json()
    assert detail["location"]["lat"] == pytest.approx(28.626)
