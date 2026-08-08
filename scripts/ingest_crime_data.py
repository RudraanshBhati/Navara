"""Build the CIP layer (and the timestamp cache the TRC layer needs) from a crime CSV.

    uv run python scripts/ingest_crime_data.py backend/data/raw/delhi_crime.csv \
        --lat-col latitude --lon-col longitude --time-col datetime --offence-col crime_type

Delhi crime datasets are wildly inconsistent in their column naming, so nothing
is hardcoded — you tell the script which columns are which. It will guess from
a list of common names if you don't.

WHAT THIS SCRIPT NEEDS, AND WHY IT MATTERS
------------------------------------------
Incident-level rows with coordinates and timestamps. Specifically:

  latitude, longitude   required. District-level aggregates cannot be gridded;
                        assigning a district's annual total to every cell in it
                        produces a layer with 15 distinct values across 6,000
                        cells, which is not a spatial signal.
  timestamp             required for TRC. Without per-incident times there is
                        no crime-by-hour histogram, and the TRC layer cannot be
                        built at all — not approximated, not degraded, not built.
  offence type          optional but valuable. Drives severity weighting; without
                        it every incident counts the same (see data/base.py).

NCRB and data.gov.in publish district-wise annual counts, which satisfy none of
the first two. See docs/DATA_SOURCES.md for what to use instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import PROCESSED_DIR  # noqa: E402
from app.data.base import normalise, severity_of  # noqa: E402
from app.data.cip import CIPLayer  # noqa: E402
from app.grid import get_grid  # noqa: E402

#: Cached incident times, keyed by cell — consumed by build_trc.py.
INCIDENT_CACHE = PROCESSED_DIR / "crime_incidents.json"

GUESSES = {
    "lat": ["latitude", "lat", "y", "lat_dd"],
    "lon": ["longitude", "lon", "lng", "long", "x", "lon_dd"],
    "time": ["datetime", "timestamp", "date_time", "occurred_on", "date", "report_date"],
    "offence": ["crime_type", "offence", "offense", "category", "crime_head", "type"],
}


def guess_column(df: pd.DataFrame, kind: str) -> str | None:
    lowered = {c.lower().strip(): c for c in df.columns}
    for candidate in GUESSES[kind]:
        if candidate in lowered:
            return lowered[candidate]
    return None


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("csv", type=Path, help="Path to the crime CSV")
    p.add_argument("--lat-col")
    p.add_argument("--lon-col")
    p.add_argument("--time-col")
    p.add_argument("--offence-col")
    p.add_argument(
        "--no-severity",
        action="store_true",
        help="Count incidents equally instead of severity-weighting them",
    )
    args = p.parse_args()

    if not args.csv.exists():
        print(f"Not found: {args.csv}")
        return 1

    df = pd.read_csv(args.csv)
    print(f"Read {len(df):,} rows from {args.csv.name}")
    print(f"Columns: {list(df.columns)}\n")

    lat_col = args.lat_col or guess_column(df, "lat")
    lon_col = args.lon_col or guess_column(df, "lon")
    time_col = args.time_col or guess_column(df, "time")
    off_col = args.offence_col or guess_column(df, "offence")

    if not lat_col or not lon_col:
        print("Could not find coordinate columns. Pass --lat-col and --lon-col.")
        print("If this dataset has no coordinates, it cannot build the CIP layer —")
        print("see the module docstring and docs/DATA_SOURCES.md.")
        return 1

    print(f"Using: lat={lat_col} lon={lon_col} time={time_col or '(none)'} offence={off_col or '(none)'}")
    if not time_col:
        print("  No timestamp column — the TRC layer cannot be built from this dataset.")
    if not off_col:
        print("  No offence column — every incident will be weighted equally.")

    grid = get_grid()
    raw: dict[str, float] = {}
    incidents: list[dict] = []
    skipped = 0

    for row in df.itertuples(index=False):
        lat = _num(getattr(row, lat_col, None))
        lon = _num(getattr(row, lon_col, None))
        if lat is None or lon is None:
            skipped += 1
            continue

        cell_id = grid.cell_id(lat, lon)
        if cell_id is None:
            skipped += 1  # outside the Delhi bbox
            continue

        offence = str(getattr(row, off_col, "")) if off_col else ""
        weight = 1.0 if args.no_severity else severity_of(offence)
        raw[cell_id] = raw.get(cell_id, 0.0) + weight

        if time_col:
            ts = pd.to_datetime(getattr(row, time_col, None), errors="coerce")
            if pd.notna(ts):
                incidents.append({"cell_id": cell_id, "hour": int(ts.hour), "weight": weight})

    if not raw:
        print("\nNo rows fell inside the Delhi grid. Check the coordinate columns.")
        return 1

    print(f"\nAggregated into {len(raw):,} cells ({skipped:,} rows skipped)")
    print(f"  raw weight range: {min(raw.values()):.2f} .. {max(raw.values()):.2f}")

    # Inverted: high crime density -> low safety contribution.
    values = normalise(raw, invert=True)

    layer = CIPLayer()
    layer.save(
        values,
        meta={
            "source": args.csv.name,
            "rows": len(df),
            "rows_used": len(df) - skipped,
            "severity_weighted": not args.no_severity,
            "has_timestamps": bool(time_col),
        },
    )
    print(f"Wrote {layer.path} ({len(values):,} cells)")

    if incidents:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        INCIDENT_CACHE.write_text(json.dumps(incidents), encoding="utf-8")
        print(f"Wrote {INCIDENT_CACHE} ({len(incidents):,} timestamped incidents)")
        print("\nNext: uv run python scripts/build_trc.py")

    return 0


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


if __name__ == "__main__":
    raise SystemExit(main())
