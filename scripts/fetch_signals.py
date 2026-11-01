"""Fetch mapped traffic signals across Delhi NCR from OpenStreetMap.

    uv run python scripts/fetch_signals.py
    uv run python scripts/fetch_signals.py --refresh

Writes backend/data/processed/signals.json: every highway=traffic_signals node
as a point. One junction usually has several, one per approach; app/features.py
merges them. Check the count printed at the end: a fallback Overpass mirror has
answered with stale data before (see docs/DATA_SOURCES.md).
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

RAW_CACHE = RAW_DIR / "ncr_signals_overpass.json"
OUT_PATH = PROCESSED_DIR / "signals.json"

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = SERVICE_BBOX
BBOX = f"{MIN_LAT},{MIN_LON},{MAX_LAT},{MAX_LON}"

QUERY = f'[out:json][timeout:180];node["highway"="traffic_signals"]({BBOX});out;'


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refresh", action="store_true")
    args = p.parse_args()

    if RAW_CACHE.exists() and not args.refresh:
        print(f"Using cached Overpass response {RAW_CACHE}")
        elements = json.loads(RAW_CACHE.read_text(encoding="utf-8"))["elements"]
    else:
        print("Querying Overpass for NCR traffic signals...")
        elements = _query(QUERY, "1/1")
        RAW_CACHE.parent.mkdir(parents=True, exist_ok=True)
        RAW_CACHE.write_text(json.dumps({"elements": elements}), encoding="utf-8")

    signals = [[round(el["lat"], 6), round(el["lon"], 6)] for el in elements]
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        json.dumps({"source": "OpenStreetMap contributors (ODbL), via Overpass", "signals": signals}),
        encoding="utf-8",
    )
    print(f"\nWrote {OUT_PATH}: {len(signals):,} signal nodes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
