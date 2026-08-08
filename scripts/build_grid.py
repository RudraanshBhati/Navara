"""Build the Delhi grid manifest and, optionally, cell -> district assignments.

    uv run python scripts/build_grid.py
    uv run python scripts/build_grid.py --districts backend/data/raw/delhi_districts.geojson

The manifest (`backend/data/processed/grid.json`) pins the bbox and cell size
that every layer is built against. Rebuilding it with different values
invalidates every ingested layer — cell ids would silently mean different
places — so the script refuses to overwrite a manifest that disagrees with the
current config unless you pass --force.

District assignment is optional. Nothing in the scoring model needs it; it is
for reporting and for the district-level crime data, which arrives keyed by
district name rather than by coordinate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import CELL_SIZE_M, DELHI_BBOX, PROCESSED_DIR  # noqa: E402
from app.grid import GRID_MANIFEST_PATH, Grid, GridSpec  # noqa: E402

DISTRICT_MAP_PATH = PROCESSED_DIR / "cell_districts.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--districts",
        type=Path,
        help="GeoJSON of Delhi district polygons. Optional.",
    )
    parser.add_argument(
        "--name-field",
        default="district",
        help="Feature property holding the district name (default: district)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite a differing manifest")
    args = parser.parse_args()

    spec = GridSpec(*DELHI_BBOX, cell_size_m=CELL_SIZE_M)

    if GRID_MANIFEST_PATH.exists() and not args.force:
        existing = GridSpec.from_file(GRID_MANIFEST_PATH)
        if existing != spec:
            print("Refusing to overwrite: the existing grid manifest differs from config.")
            print(f"  on disk : {existing}")
            print(f"  config  : {spec}")
            print("\nEvery built layer is keyed to the manifest on disk. Rebuilding the")
            print("grid means rebuilding all five layers. Pass --force if that is what")
            print("you mean to do.")
            return 1

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    GRID_MANIFEST_PATH.write_text(spec.to_json(), encoding="utf-8")

    grid = Grid(spec)
    print(f"Wrote {GRID_MANIFEST_PATH}")
    print(f"  {grid.n_rows} rows x {grid.n_cols} cols = {grid.n_cells} cells")
    print(f"  cell size: {spec.cell_size_m:.0f} m")

    if args.districts:
        assign_districts(grid, args.districts, args.name_field)

    return 0


def assign_districts(grid: Grid, geojson_path: Path, name_field: str) -> None:
    try:
        from shapely.geometry import shape, Point
        from shapely.strtree import STRtree
    except ImportError:
        print("shapely is required for --districts (it is in the base dependencies)")
        return

    if not geojson_path.exists():
        print(f"District file not found: {geojson_path}")
        print("See docs/DATA_SOURCES.md for where to get it.")
        return

    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    features = data.get("features", [])
    if not features:
        print("No features in the district GeoJSON")
        return

    geoms = [shape(f["geometry"]) for f in features]
    names = [
        f.get("properties", {}).get(name_field)
        or f.get("properties", {}).get("DISTRICT")
        or f.get("properties", {}).get("NAME")
        or f"district_{i}"
        for i, f in enumerate(features)
    ]
    tree = STRtree(geoms)

    mapping: dict[str, str] = {}
    for cell_id in grid.all_cell_ids():
        lat, lon = grid.cell_center(cell_id)
        point = Point(lon, lat)  # GeoJSON is (lon, lat)
        for idx in tree.query(point):
            if geoms[idx].contains(point):
                mapping[cell_id] = names[idx]
                break

    DISTRICT_MAP_PATH.write_text(json.dumps(mapping), encoding="utf-8")
    inside = len(mapping)
    print(f"Wrote {DISTRICT_MAP_PATH}")
    print(f"  {inside}/{grid.n_cells} cells fall inside a district polygon")
    print(f"  ({grid.n_cells - inside} are bbox padding outside the NCT boundary)")


if __name__ == "__main__":
    raise SystemExit(main())
