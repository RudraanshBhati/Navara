"""Build the crowd layer (CDS) from every input that is available.

    uv run python scripts/ingest_population.py     # people per cell (once)
    uv run python scripts/ingest_osm_poi.py        # POIs (once; cached)
    # optional: Delhi GTFS zips in backend/data/raw/gtfs/  (see below)
    uv run python scripts/build_cds.py

Combines, per cell and per hour (see combine_presence in app/data/cds.py):

  population   residents, damped mildly at night
  POIs         shops and amenities, following the activity curve
  transit      bus/metro departures in that hour, from the timetable

Any input that is missing is left out and its weight goes to the others; the
script says which it used. Run it again whenever an input changes.

TRANSIT TIMETABLES (optional, recommended)
------------------------------------------
Delhi publishes GTFS for DTC/cluster buses and for the Metro at
https://otd.delhi.gov.in/data/static/ and /data/staticDMRC/. Downloading
needs a short form (name, email, purpose) and accepting their terms, so it is
a manual step. Save each zip into backend/data/raw/gtfs/ — any filename.

Two caveats. The bus feed's stop times are estimated from a constant speed,
which is fine for counting departures per hour but not for anything finer.
And every trip in the feed is counted regardless of which days it runs, so
the hourly shape is an all-week average rather than a specific day's.
"""

from __future__ import annotations

import csv
import io
import json
import math
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import RAW_DIR  # noqa: E402
from app.data.base import normalise  # noqa: E402
from app.data.cds import CDSLayer, combine_presence  # noqa: E402
from app.grid import get_grid  # noqa: E402
from ingest_osm_poi import DEFAULT_CACHE as POI_CACHE  # noqa: E402
from ingest_osm_poi import poi_scores  # noqa: E402

POPULATION_PATH = RAW_DIR / "population_cells.json"
GTFS_DIR = RAW_DIR / "gtfs"


def load_population() -> dict[str, float] | None:
    if not POPULATION_PATH.exists():
        return None
    return json.loads(POPULATION_PATH.read_text(encoding="utf-8"))["cells"]


def load_pois() -> dict[str, float] | None:
    if not POI_CACHE.exists():
        return None
    elements = json.loads(POI_CACHE.read_text(encoding="utf-8"))["elements"]
    raw, _counted = poi_scores(elements)
    return raw


def departures_per_cell_hour(zips: list[Path]) -> dict[str, list[float]]:
    """Scheduled departures per cell per hour of day, summed over every feed."""
    grid = get_grid()
    out: dict[str, list[float]] = {}

    for path in zips:
        with zipfile.ZipFile(path) as zf:
            names = {Path(n).name: n for n in zf.namelist()}
            if "stops.txt" not in names or "stop_times.txt" not in names:
                print(f"  skip {path.name}: no stops.txt / stop_times.txt inside")
                continue

            stop_cell: dict[str, str] = {}
            for row in _rows(zf, names["stops.txt"]):
                try:
                    cid = grid.cell_id(float(row["stop_lat"]), float(row["stop_lon"]))
                except (KeyError, ValueError):
                    continue
                if cid:
                    stop_cell[row["stop_id"]] = cid

            counted = 0
            for row in _rows(zf, names["stop_times.txt"]):
                cid = stop_cell.get(row.get("stop_id", ""))
                hour = _hour(row.get("departure_time") or row.get("arrival_time") or "")
                if cid is None or hour is None:
                    continue
                out.setdefault(cid, [0.0] * 24)[hour] += 1
                counted += 1
            print(f"  {path.name}: {len(stop_cell):,} stops in the grid, {counted:,} departures")

    return out


def normalise_hourly(raw: dict[str, list[float]]) -> dict[str, list[float]]:
    """Scale every cell-hour against one city-wide reference.

    One scale for all hours, not one per hour: a stop that sees 4 buses at
    11pm should score lower than the same stop at 6pm, and per-hour scaling
    would make the busiest stop at 3am look as busy as rush hour.

    Scaled from zero, not with `normalise`, which maps the smallest value to
    0 — that would score the one late bus on a quiet route the same as no
    service at all, at exactly the hours where one bus is the difference.
    """
    values = sorted(v for hours in raw.values() for v in hours if v > 0)
    if not values:
        return {}
    ref = values[min(int(len(values) * 0.95), len(values) - 1)]
    return {cid: [min(1.0, v / ref) for v in hours] for cid, hours in raw.items()}


def main() -> int:
    grid = get_grid()
    sources: dict[str, str] = {}

    population = load_population()
    if population is not None:
        sources["population"] = f"{sum(population.values()) / 1e6:.1f} M people, {len(population):,} cells"
        # Square root before scaling: eyes on the street has diminishing
        # returns. Linear scaling against the densest 5% of cells put a
        # median colony (~1,300 residents) at 0.13 and flagged nearly every
        # night route as deserted; log scaling overcorrected and lifted near-
        # empty fields (~50 residents) to 0.28. The root puts a cell at
        # Delhi's mean density near 0.5 and leaves empty land near zero.
        population = normalise({cid: math.sqrt(v) for cid, v in population.items()})

    poi = load_pois()
    if poi is not None:
        sources["poi"] = f"{len(poi):,} cells with mapped POIs"
        poi = normalise(poi)

    transit = None
    zips = sorted(GTFS_DIR.glob("*.zip")) if GTFS_DIR.exists() else []
    if zips:
        print("Reading transit timetables:")
        raw = departures_per_cell_hour(zips)
        if raw:
            transit = normalise_hourly(raw)
            sources["transit"] = f"{len(zips)} feed(s), {len(raw):,} cells with stops"

    if not sources:
        print("No inputs found. Run ingest_population.py and/or ingest_osm_poi.py first.")
        return 1

    hourly = combine_presence(grid.all_cell_ids(), population, poi, transit)

    layer = CDSLayer()
    layer.save_hourly(hourly, meta={"source": "build_cds", "inputs": sources, "cells": len(hourly)})

    print(f"\nWrote {layer.path}")
    for name in ("population", "poi", "transit"):
        print(f"  {name:<10} {sources.get(name, 'not available — weight shared among the others')}")
    if "transit" not in sources:
        print(f"\n  For the hourly transit term, put Delhi GTFS zips in {GTFS_DIR}")
        print("  (https://otd.delhi.gov.in/data/static/ — needs a short form).")
    print(f"  {len(hourly):,} of {grid.n_cells:,} cells scored")
    return 0


def _rows(zf: zipfile.ZipFile, name: str):
    with zf.open(name) as fh:
        yield from csv.DictReader(io.TextIOWrapper(fh, encoding="utf-8-sig"))


def _hour(value: str) -> int | None:
    """GTFS times run past 24:00 for trips that cross midnight."""
    try:
        return int(value.strip().split(":")[0]) % 24
    except (ValueError, IndexError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
