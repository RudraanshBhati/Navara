"""Build the TRC layer from the crime timestamps cached by ingest_crime_data.py.

    uv run python scripts/build_trc.py

Produces two things:

  a city-wide 24-bin curve   always
  per-cell 24-bin curves     only for cells with enough incidents to justify one

The per-cell threshold is the point of the script. With 500 m cells, most cells
hold a handful of incidents, and an hourly histogram over eight events is noise
that looks like signal — it will confidently claim 3am is safe in a cell simply
because nothing was recorded at 3am there. Cells under the threshold fall back
to the city curve, which is what MIN_INCIDENTS_FOR_CELL_CURVE is for.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.data.trc import MIN_INCIDENTS_FOR_CELL_CURVE, TRCLayer  # noqa: E402

INCIDENT_CACHE = Path(__file__).resolve().parent.parent / "backend/data/processed/crime_incidents.json"


def curve_from_counts(counts: list[float]) -> list[float]:
    """Hourly weighted counts -> safety contribution per hour, 1 = safest hour.

    Normalised against the *peak* hour rather than the total, so the curve says
    "how bad is this hour compared with the worst hour" — a shape that survives
    differences in dataset size, unlike a share-of-total which shrinks as you
    add years of data.
    """
    peak = max(counts) if counts else 0.0
    if peak <= 0:
        return [0.5] * 24
    return [round(1.0 - (c / peak), 4) for c in counts]


def main() -> int:
    if not INCIDENT_CACHE.exists():
        print(f"Not found: {INCIDENT_CACHE}")
        print("Run scripts/ingest_crime_data.py first, with a dataset that has timestamps.")
        return 1

    incidents = json.loads(INCIDENT_CACHE.read_text(encoding="utf-8"))
    if not incidents:
        print("Incident cache is empty — the crime dataset had no parseable timestamps.")
        return 1

    global_counts = [0.0] * 24
    per_cell: dict[str, list[float]] = {}
    per_cell_totals: dict[str, int] = {}

    for inc in incidents:
        hour = inc["hour"]
        weight = float(inc.get("weight", 1.0))
        cell_id = inc["cell_id"]

        global_counts[hour] += weight
        per_cell.setdefault(cell_id, [0.0] * 24)[hour] += weight
        per_cell_totals[cell_id] = per_cell_totals.get(cell_id, 0) + 1

    global_curve = curve_from_counts(global_counts)

    cell_curves = {
        cell_id: curve_from_counts(counts)
        for cell_id, counts in per_cell.items()
        if per_cell_totals[cell_id] >= MIN_INCIDENTS_FOR_CELL_CURVE
    }

    layer = TRCLayer()
    layer.save_curves(
        global_curve,
        cell_curves,
        meta={
            "incidents": len(incidents),
            "cells_with_own_curve": len(cell_curves),
            "min_incidents_for_cell_curve": MIN_INCIDENTS_FOR_CELL_CURVE,
        },
    )

    print(f"Built TRC from {len(incidents):,} timestamped incidents")
    print(f"  {len(cell_curves):,} of {len(per_cell):,} cells had enough data for their own curve")
    print("\nCity-wide risk by hour (lower = more dangerous):")
    for hour in range(24):
        bar = "#" * int(global_counts[hour] / max(max(global_counts), 1) * 40)
        print(f"  {hour:02d}:00  {global_curve[hour]:.2f}  {bar}")

    worst = min(range(24), key=lambda h: global_curve[h])
    print(f"\nRiskiest hour city-wide: {worst:02d}:00")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
