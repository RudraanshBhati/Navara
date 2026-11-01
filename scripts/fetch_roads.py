"""Fetch Delhi NCR's main roads from OpenStreetMap, for the road-type term.

    uv run python scripts/fetch_roads.py
    uv run python scripts/fetch_roads.py --refresh

Writes backend/data/processed/roads_major.json: every motorway, trunk,
primary, secondary and tertiary road (and their slip roads) as a polyline with
its class. A route segment within a few metres of one of these is on a main
road; one that is not is on a smaller internal road.

Only the major classes are fetched, which is what keeps this small. The
smaller roads do not need to be downloaded to know a segment is on one: OSM's
coverage of Delhi's main roads is near complete, so "not near any main road"
is a reliable "on a lane or internal road".
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import PROCESSED_DIR, RAW_DIR, SERVICE_BBOX  # noqa: E402
from fetch_map_pois import _query  # noqa: E402

RAW_CACHE = RAW_DIR / "ncr_major_roads_overpass.json"
OUT_PATH = PROCESSED_DIR / "roads_major.json"

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = SERVICE_BBOX
BBOX = f"{MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON}"

CLASSES = ("motorway", "trunk", "primary", "secondary", "tertiary")

QUERY = (
    "[out:json][timeout:300];"
    f'way["highway"~"^({"|".join(CLASSES)})(_link)?$"]({BBOX});'
    "out tags geom;"
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    if RAW_CACHE.exists() and not args.refresh:
        print(f"Using cached Overpass response {RAW_CACHE}")
        elements = json.loads(RAW_CACHE.read_text(encoding="utf-8"))["elements"]
    else:
        print("Querying Overpass for NCR main roads...")
        elements = _query(QUERY, "1/1")
        RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RAW_CACHE.write_text(json.dumps({"elements": elements}), encoding="utf-8")

    roads = []
    counts: dict[str, int] = {}
    for el in elements:
        geom = el.get("geometry")
        cls = el.get("tags", {}).get("highway", "").removesuffix("_link")
        if not geom or cls not in CLASSES:
            continue
        roads.append({"cls": cls, "pts": [[round(g["lat"], 6), round(g["lon"], 6)] for g in geom]})
        counts[cls] = counts.get(cls, 0) + 1

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"source": "OpenStreetMap contributors (ODbL), via Overpass", "roads": roads}),
        encoding="utf-8",
    )
    print(f"\nWrote {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.1f} MB)")
    for cls in CLASSES:
        print(f"  {cls:<10} {counts.get(cls, 0):>7,} ways")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
