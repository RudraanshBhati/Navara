"""Fetch the places the map shows — hospitals, police, transit, shops — for Delhi NCR.

    uv run python scripts/fetch_map_pois.py
    uv run python scripts/fetch_map_pois.py --refresh     # re-query Overpass

Writes backend/data/processed/map_pois.json, which GET /pois serves and the
offline place search reads. This is display data, kept apart from the crowd
layer's POI cache on purpose: the map wants all of NCR, police stations drawn
as buildings, every bus stop; the crowd layer wants a fixed, reproducible
input. Changing what the map shows must not silently move a safety score.

Overpass is a shared volunteer service. One query, cached; do not loop it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import PROCESSED_DIR, RAW_DIR, SERVICE_BBOX  # noqa: E402
from app.pois import classify  # noqa: E402
from ingest_osm_poi import OVERPASS_URL, USER_AGENT  # noqa: E402

RAW_CACHE = RAW_DIR / "ncr_map_pois_overpass.json"
OUT_PATH = PROCESSED_DIR / "map_pois.json"

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = SERVICE_BBOX
BBOX = f"{MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON}"

# `nwr` + `out center`: hospitals, police stations and malls are mostly mapped
# as building outlines, not points. Asking for nodes only — as the crowd
# layer's query does — misses most of them.
#
# Three queries rather than one: a single NCR-wide query for all of this came
# back 502 from the gateway (the server gives up before Overpass does).
_SELECTORS = (
    f'nwr["amenity"~"^(hospital|clinic|police|bus_station|pharmacy|fuel|toilets)$"]({BBOX});'
    f'node["highway"="bus_stop"]({BBOX});'
    f'nwr["railway"="station"]({BBOX});',
    f'nwr["amenity"~"^(restaurant|cafe|fast_food|atm|bank)$"]({BBOX});',
    f'nwr["shop"]["name"]({BBOX});',
)
QUERIES = [f"[out:json][timeout:300];({sel});out center tags;" for sel in _SELECTORS]

MIRRORS = (
    OVERPASS_URL,
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

#: OSM tags worth showing on a place card. Everything else is dropped to keep
#: the file small enough to load into the API process.
KEEP_TAGS = (
    # amenity separates a 24-hour hospital from a daytime clinic, which the
    # open-places term needs; both display under the one "hospital" category.
    "amenity",
    "opening_hours",
    "phone",
    "contact:phone",
    "website",
    "contact:website",
    "addr:housenumber",
    "addr:street",
    "addr:suburb",
    "addr:city",
    "addr:postcode",
    "operator",
    "emergency",
    "healthcare",
    "shop",
    "cuisine",
    "network",
    "brand",
    "wheelchair",
)


def fetch(refresh: bool) -> list[dict]:
    if RAW_CACHE.exists() and not refresh:
        print(f"Using cached Overpass response {RAW_CACHE}")
        return json.loads(RAW_CACHE.read_text(encoding="utf-8"))["elements"]

    print("Querying Overpass for Delhi NCR — a few minutes for an area this size...")
    elements: list[dict] = []
    for i, query in enumerate(QUERIES, 1):
        elements.extend(_query(query, f"{i}/{len(QUERIES)}"))

    RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
    RAW_CACHE.write_text(json.dumps({"elements": elements}), encoding="utf-8")
    return elements


def _query(query: str, label: str) -> list[dict]:
    """One query, trying each mirror in turn before giving up."""
    last: Exception | None = None
    for url in MIRRORS:
        start = time.time()
        try:
            r = httpx.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=600.0)
            r.raise_for_status()
            found = r.json()["elements"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            print(f"  query {label}: {url} failed ({exc.__class__.__name__}), trying next")
            last = exc
            time.sleep(5)
            continue
        print(f"  query {label}: {len(found):,} elements in {time.time() - start:.0f}s")
        return found
    raise SystemExit(f"Every Overpass mirror failed for query {label}: {last}")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    elements = fetch(args.refresh)
    pois: list[dict] = []
    counts: dict[str, int] = {}

    for el in elements:
        tags = el.get("tags", {})
        category = classify(tags)
        if category is None:
            continue
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        pois.append(
            {
                "id": f"{el['type'][0]}{el['id']}",
                "lat": round(lat, 6),
                "lng": round(lon, 6),
                "category": category,
                "name": tags.get("name:en") or tags.get("name") or "",
                "tags": {k: tags[k] for k in KEEP_TAGS if k in tags},
            }
        )
        counts[category] = counts.get(category, 0) + 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"source": "OpenStreetMap contributors (ODbL), via Overpass", "pois": pois}),
        encoding="utf-8",
    )
    print(f"\nWrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.1f} MB)")
    for cat, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {cat:<10} {n:>7,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
