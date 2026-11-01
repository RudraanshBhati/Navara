"""Fetch mapped CCTV cameras across Delhi NCR from OpenStreetMap.

    uv run python scripts/fetch_cameras.py
    uv run python scripts/fetch_cameras.py --refresh

Writes backend/data/processed/cameras.json: every man_made=surveillance node
or way, as a point. Volunteers mapped these, so a missing camera means nobody
mapped it, not that the street has none. See app/features.py for how that
limits what the app may say about them.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_map_pois import _query  # noqa: E402

from app.config import PROCESSED_DIR, RAW_DIR, SERVICE_BBOX  # noqa: E402

RAW_CACHE = RAW_DIR / "ncr_cameras_overpass.json"
OUT_PATH = PROCESSED_DIR / "cameras.json"

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = SERVICE_BBOX
BBOX = f"{MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON}"

QUERY = f'[out:json][timeout:180];nwr["man_made"="surveillance"]({BBOX});out center tags;'


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    if RAW_CACHE.exists() and not args.refresh:
        print(f"Using cached Overpass response {RAW_CACHE}")
        elements = json.loads(RAW_CACHE.read_text(encoding="utf-8"))["elements"]
    else:
        print("Querying Overpass for NCR cameras...")
        elements = _query(QUERY, "1/1")
        RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RAW_CACHE.write_text(json.dumps({"elements": elements}), encoding="utf-8")

    cameras = []
    for el in elements:
        lat = el.get("lat") or el.get("center", {}).get("lat")
        lng = el.get("lon") or el.get("center", {}).get("lon")
        if lat is None or lng is None:
            continue
        tags = el.get("tags", {})
        # Indoor cameras watch a room, not a street.
        if tags.get("surveillance") == "indoor":
            continue
        cameras.append([round(lat, 6), round(lng, 6)])

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"source": "OpenStreetMap contributors (ODbL), via Overpass", "cameras": cameras}),
        encoding="utf-8",
    )
    print(f"\nWrote {OUT_PATH}: {len(cameras):,} outdoor cameras of {len(elements):,} mapped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
