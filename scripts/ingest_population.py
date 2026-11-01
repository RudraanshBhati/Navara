"""People per grid cell, from Meta/CIESIN's High Resolution Settlement Layer.

    uv pip install -e ".[raster]"                 # rasterio is an optional extra
    uv run python scripts/ingest_population.py

An input to the crowd layer, not a layer itself: writes
backend/data/raw/population_cells.json, which scripts/build_cds.py reads.

WHAT IT IS
----------
Estimated residents per 30 m pixel (1 arc-second), built by detecting
buildings in satellite imagery and distributing census totals across them.
Free, no key, CC BY 4.0. Cite: Meta and CIESIN, High Resolution Settlement
Layer. https://registry.opendata.aws/dataforgood-fb-hrsl/

It is read straight from the public AWS bucket as a cloud-optimised GeoTIFF,
so only the Delhi window is fetched — a few tens of MB, not the whole
country. The window is cached to raw/ so a rerun does not refetch it.

WHAT IT IS NOT
--------------
Residents, not people on the street. A dense residential block at 2am is full
of people who are indoors and asleep. That is why build_cds.py damps this
term at night less than it damps POIs but does not treat it as footfall. And
the census totals it distributes predate recent growth, so fast-growing edges
of the city are likely undercounted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import RAW_DIR  # noqa: E402
from app.grid import get_grid  # noqa: E402

HRSL_URL = (
    "/vsicurl/https://dataforgood-fb-data.s3.amazonaws.com/"
    "hrsl-cogs/hrsl_general/hrsl_general-latest.vrt"
)
WINDOW_CACHE = RAW_DIR / "hrsl_population_delhi.tif"
OUT_PATH = RAW_DIR / "population_cells.json"

#: Stop GDAL listing the bucket on open — thousands of keys, minutes of delay.
GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".vrt,.tif",
}


def fetch_window(bbox: tuple[float, float, float, float], refresh: bool) -> Path:
    """Clip the HRSL to `bbox` and cache it as a local GeoTIFF."""
    import rasterio
    from rasterio.windows import from_bounds

    if WINDOW_CACHE.exists() and not refresh:
        print(f"Using cached population window {WINDOW_CACHE}")
        return WINDOW_CACHE

    print("Reading the HRSL window over Delhi from AWS...")
    start = time.time()
    with rasterio.Env(**GDAL_ENV), rasterio.open(HRSL_URL) as src:
        window = from_bounds(*bbox, transform=src.transform)
        data = src.read(1, window=window).astype("float32")
        profile = {
            "driver": "GTiff",
            "dtype": "float32",
            "count": 1,
            "width": data.shape[1],
            "height": data.shape[0],
            "crs": src.crs,
            "transform": src.window_transform(window),
            "nodata": float("nan"),
            "compress": "deflate",
        }

    WINDOW_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(WINDOW_CACHE, "w", **profile) as dst:
        dst.write(data, 1)
    size = WINDOW_CACHE.stat().st_size / 1e6
    print(f"  {data.shape[1]}x{data.shape[0]} px in {time.time() - start:.0f}s -> {WINDOW_CACHE} ({size:.0f} MB)")
    return WINDOW_CACHE


def people_per_cell(path: Path) -> dict[str, float]:
    """Sum every pixel into the cell containing its centre."""
    import numpy as np
    import rasterio

    grid = get_grid()
    spec = grid.spec

    with rasterio.open(path) as src:
        data = src.read(1)
        t = src.transform

    data = np.nan_to_num(data, nan=0.0)
    rows_px, cols_px = np.indices(data.shape)
    # Pixel centres, then the same arithmetic Grid.row_col uses.
    lons = t.c + (cols_px + 0.5) * t.a
    lats = t.f + (rows_px + 0.5) * t.e
    row = np.floor((lats - spec.min_lat) / grid.deg_lat).astype(int)
    col = np.floor((lons - spec.min_lon) / grid.deg_lon).astype(int)
    inside = (row >= 0) & (row < grid.n_rows) & (col >= 0) & (col < grid.n_cols) & (data > 0)

    flat = row[inside] * grid.n_cols + col[inside]
    sums = np.bincount(flat, weights=data[inside], minlength=grid.n_rows * grid.n_cols)

    return {
        grid.encode(int(i) // grid.n_cols, int(i) % grid.n_cols): round(float(v), 1)
        for i, v in enumerate(sums)
        if v > 0
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--refresh", action="store_true", help="Refetch the window from AWS")
    args = p.parse_args()

    try:
        spec = get_grid().spec
        path = fetch_window((spec.min_lon, spec.min_lat, spec.max_lon, spec.max_lat), args.refresh)
        cells = people_per_cell(path)
    except ImportError:
        print("rasterio is not installed. It is an optional extra:")
        print('  uv pip install -e ".[raster]"')
        return 1

    OUT_PATH.write_text(
        json.dumps({"source": "Meta/CIESIN HRSL (CC BY 4.0)", "unit": "people", "cells": cells}),
        encoding="utf-8",
    )
    grid = get_grid()
    total = sum(cells.values())
    print(f"\nWrote {OUT_PATH}")
    print(f"  {total / 1e6:.1f} M people across {len(cells):,} of {grid.n_cells:,} cells")
    print(f"  {grid.n_cells - len(cells):,} cells have no detected settlement (fields, ridge, river)")
    print("\nNext: uv run python scripts/build_cds.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
