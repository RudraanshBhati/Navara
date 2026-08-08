"""Build the NTLS layer from a VIIRS night-lights raster.

    uv pip install -e ".[raster]"          # rasterio is an optional extra
    uv run python scripts/ingest_viirs.py backend/data/raw/delhi_viirs.tif

Samples mean radiance per grid cell and normalises. Not inverted: brighter is
safer.

GETTING THE RASTER
------------------
The product is VNP46A2 ("Black Marble", gap-filled BRDF-corrected night-time
radiance), 500 m native resolution — which happens to match our cell size, so
no resampling judgement is needed.

Two routes, either is fine:

  NASA Black Marble  https://blackmarble.gsfc.nasa.gov/
                     Needs only a free Earthdata login (instant). Download the
                     tile covering Delhi (h26v06) and clip it. Recommended.

  Google Earth Engine  asset NASA/VIIRS/002/VNP46A2, band `Gap_Filled_DNB_BRDF_Corrected_NTL`
                     Needs an approved GEE account (1-2 day wait). Better if you
                     want to composite many nights server-side.

Either way, prefer a multi-night median composite over a single night. One
night's raster carries cloud artefacts and moonlight variation that will show up
as fake dark patches — and a fake dark patch here becomes a "poorly lit stretch"
warning on someone's route.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.data.base import normalise  # noqa: E402
from app.data.ntls import NTLSLayer  # noqa: E402
from app.grid import get_grid  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("raster", type=Path, help="GeoTIFF of night radiance covering Delhi")
    p.add_argument("--band", type=int, default=1)
    args = p.parse_args()

    try:
        import numpy as np
        import rasterio
        from rasterio.warp import transform as warp_transform
    except ImportError:
        print("rasterio is not installed. It is an optional extra:")
        print('  uv pip install -e ".[raster]"')
        return 1

    if not args.raster.exists():
        print(f"Not found: {args.raster}")
        print("See the module docstring for where to download it.")
        return 1

    grid = get_grid()

    with rasterio.open(args.raster) as src:
        print(f"Raster: {src.width}x{src.height}, crs={src.crs}, bands={src.count}")
        band = src.read(args.band, masked=True)

        # Sample at each cell centre. At 500 m native resolution against 500 m
        # cells this is one raster pixel per cell — averaging a window would
        # blur the layer without adding information.
        cell_ids = grid.all_cell_ids()
        lats, lons = zip(*(grid.cell_center(c) for c in cell_ids), strict=True)

        if src.crs and src.crs.to_epsg() != 4326:
            xs, ys = warp_transform("EPSG:4326", src.crs, list(lons), list(lats))
        else:
            xs, ys = list(lons), list(lats)

        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)

    raw: dict[str, float] = {}
    outside = 0

    for cell_id, r, c in zip(cell_ids, rows, cols, strict=True):
        if not (0 <= r < band.shape[0] and 0 <= c < band.shape[1]):
            outside += 1
            continue
        value = band[r, c]
        if np.ma.is_masked(value):
            continue
        raw[cell_id] = float(value)

    if not raw:
        print("No cells sampled — does this raster actually cover Delhi?")
        return 1

    values = normalise(raw, invert=False)

    layer = NTLSLayer()
    layer.save(
        values,
        meta={"source": args.raster.name, "band": args.band, "cells": len(values)},
    )
    print(f"\nWrote {layer.path}")
    print(f"  sampled {len(raw):,} of {grid.n_cells:,} cells ({outside:,} outside the raster)")
    print(f"  raw radiance range: {min(raw.values()):.2f} .. {max(raw.values()):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
