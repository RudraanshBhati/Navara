"""Build the CDS layer from OpenStreetMap POI density via the Overpass API.

    uv run python scripts/ingest_osm_poi.py
    uv run python scripts/ingest_osm_poi.py --cache backend/data/raw/delhi_pois.json

Counts amenities that imply people are around, per grid cell, then normalises.
Not inverted: more POIs -> higher crowd proxy -> safer.

Read app/data/cds.py before trusting the output. This is the weakest layer in
the model and it is weighted lowest (0.10) for reasons that are worth
understanding rather than working around.

Overpass is a shared free service. Be polite: the query is cached to disk on
first run and reused, and there is a single request rather than one per cell.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import DELHI_BBOX, RAW_DIR  # noqa: E402
from app.data.base import normalise  # noqa: E402
from app.data.cds import CDSLayer  # noqa: E402
from app.grid import get_grid  # noqa: E402

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_CACHE = RAW_DIR / "delhi_pois.json"

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = DELHI_BBOX

# Weighted by how much each POI type implies sustained street presence rather
# than a brief visit. A 24-hour hospital or a metro entrance keeps a street
# populated in a way a dentist's office does not.
POI_WEIGHTS: dict[str, float] = {
    "hospital": 3.0,
    "police": 3.0,
    "fuel": 2.5,
    "pharmacy": 2.0,
    "restaurant": 2.0,
    "fast_food": 2.0,
    "cafe": 1.5,
    "bar": 1.5,
    "marketplace": 2.5,
    "bank": 1.0,
    "atm": 1.0,
    "school": 1.0,
    "college": 1.5,
    "university": 1.5,
    "place_of_worship": 1.0,
    "bus_station": 2.5,
    "cinema": 1.5,
}

QUERY = f"""
[out:json][timeout:180];
(
  node["amenity"]({MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON});
  node["shop"]({MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON});
  node["public_transport"="station"]({MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON});
  node["railway"="subway_entrance"]({MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON});
);
out body;
"""


def fetch_pois(cache: Path, refresh: bool) -> list[dict]:
    if cache.exists() and not refresh:
        print(f"Using cached POIs from {cache}")
        return json.loads(cache.read_text(encoding="utf-8"))["elements"]

    print("Querying Overpass — this takes a minute or two for a city-sized bbox...")
    start = time.time()
    r = httpx.post(OVERPASS_URL, data={"data": QUERY}, timeout=300.0)
    r.raise_for_status()
    payload = r.json()

    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload), encoding="utf-8")
    print(f"Fetched {len(payload['elements']):,} elements in {time.time() - start:.0f}s -> {cache}")
    return payload["elements"]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    p.add_argument("--refresh", action="store_true", help="Re-query Overpass, ignoring the cache")
    args = p.parse_args()

    elements = fetch_pois(args.cache, args.refresh)
    grid = get_grid()

    raw: dict[str, float] = {}
    counted = 0

    for el in elements:
        lat, lon = el.get("lat"), el.get("lon")
        if lat is None or lon is None:
            continue
        cell_id = grid.cell_id(lat, lon)
        if cell_id is None:
            continue

        tags = el.get("tags", {})
        kind = tags.get("amenity") or tags.get("public_transport") or tags.get("railway")
        # A generic shop is still a sign of life, just a weaker one.
        weight = POI_WEIGHTS.get(kind, 1.0 if tags.get("shop") else 0.5)

        raw[cell_id] = raw.get(cell_id, 0.0) + weight
        counted += 1

    if not raw:
        print("No POIs fell inside the grid — check the bbox.")
        return 1

    values = normalise(raw, invert=False)

    layer = CDSLayer()
    layer.save(
        values,
        meta={
            "source": "overpass",
            "elements": len(elements),
            "counted": counted,
            "cells": len(values),
        },
    )
    print(f"\nWrote {layer.path}")
    print(f"  {counted:,} POIs across {len(values):,} of {grid.n_cells:,} cells")
    print(f"  {grid.n_cells - len(values):,} cells have no mapped POI at all — those score")
    print("  NEUTRAL, not zero, because absent OSM coverage is not evidence of emptiness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
