"""Build the NTLS layer from VIIRS night-lights granules.

    uv pip install -e ".[raster]"                       # rasterio is an optional extra
    python scripts/download_viirs.py                    # 12 monthly composites
    python scripts/ingest_viirs.py backend/data/raw/VNP46A3*.h5

Samples radiance at each grid cell centre, takes the **median across every
granule given**, and normalises. Not inverted: brighter is safer. Monthly
composites (VNP46A3) carry a quality flag; samples it marks as gap-filled or
built from three nights or fewer are dropped before the median.

Do not mix products in one run: monthly and nightly radiance are on
different footings, and a median across both means nothing.

PASS MANY GRANULES, NOT ONE
---------------------------
One night's raster carries cloud shadow and moonlight variation, and a dark
patch from a passing cloud is indistinguishable from an unlit street. The
per-cell median across a dozen nights makes that transient rather than
permanent. A single granule still works and the script will say what it is
doing, but treat the result as a smoke test rather than a layer.

The median is taken per cell rather than per raster pixel because cells are what
the model scores, and it saves aligning rasters that may differ in extent.

INPUT FORMATS
-------------
Native `.h5` granules straight from NASA are read directly — the script picks
the `Gap_Filled_DNB_BRDF-Corrected_NTL` subdataset out of the HDF5 container,
which is georeferenced (EPSG:4326) even though the container itself is not. No
conversion to GeoTIFF is needed. A plain GeoTIFF also works, for a raster you
have already clipped or composited yourself.

Radiance is used unscaled. VNP46A2 stores NTL with a 0.1 scale factor, but
`normalise` is a monotonic linear rescale to [0, 1], so a constant multiplier
cannot change the output.
"""

from __future__ import annotations

import argparse
import glob as globlib
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.data.base import normalise  # noqa: E402
from app.data.ntls import NTLSLayer  # noqa: E402
from app.grid import get_grid  # noqa: E402

#: Radiance subdatasets in order of preference; a granule holds only its own
#: product's. Nightly VNP46A2: the gap-filled band has had cloud-contaminated
#: retrievals interpolated over, the raw band leaves holes. Monthly VNP46A3:
#: the near-nadir snow-free composite, which is what NASA's own tools default
#: to — off-nadir views stretch a pixel and smear bright roads into dark lanes.
SUBDATASET_KEYS = (
    "Gap_Filled_DNB_BRDF-Corrected_NTL",
    "DNB_BRDF-Corrected_NTL",
    "NearNadir_Composite_Snow_Free",
)

#: radiance subdataset -> (its quality subdataset, the flag values to keep).
#: For the monthly composite: 0 = more than three clear nights went in,
#: 1 = three or fewer, 2 = gap-filled from history. Only 0 is kept. A
#: gap-filled pixel in a monsoon month is an older month's value wearing this
#: month's date, and the median across the year is better off without it.
QUALITY_KEYS: dict[str, tuple[str, frozenset[int]]] = {
    "NearNadir_Composite_Snow_Free": ("NearNadir_Composite_Snow_Free_Quality", frozenset({0})),
}


def resolve_source(path: Path) -> tuple[str, tuple[str, frozenset[int]] | None]:
    """What rasterio should open — an HDF5 subdataset, or the path — plus the
    quality subdataset and kept flag values, when the product has one."""
    if path.suffix.lower() not in (".h5", ".hdf5", ".he5"):
        return str(path), None

    import rasterio

    with rasterio.open(path) as container:
        subs = container.subdatasets
    by_name = {sub.rsplit("/", 1)[-1]: sub for sub in subs}

    for key in SUBDATASET_KEYS:
        if key in by_name:
            quality = QUALITY_KEYS.get(key)
            if quality and quality[0] in by_name:
                return by_name[key], (by_name[quality[0]], quality[1])
            return by_name[key], None

    raise SystemExit(
        f"{path.name}: no night-lights subdataset found.\n"
        f"  looked for: {', '.join(SUBDATASET_KEYS)}\n"
        f"  found     : {', '.join(s.rsplit('/', 1)[-1] for s in subs) or '(none)'}"
    )


def sample(
    source: str,
    cell_ids: list[str],
    lats,
    lons,
    band_index: int,
    quality: tuple[str, frozenset[int]] | None = None,
) -> tuple[dict[str, float], int]:
    """Radiance at each cell centre for one granule, and how many cells the
    quality flag rejected."""
    import numpy as np
    import rasterio
    from rasterio.warp import transform as warp_transform

    with rasterio.open(source) as src:
        band = src.read(band_index, masked=True)

        if src.crs and src.crs.to_epsg() != 4326:
            xs, ys = warp_transform("EPSG:4326", src.crs, list(lons), list(lats))
        else:
            xs, ys = list(lons), list(lats)

        rows, cols = rasterio.transform.rowcol(src.transform, xs, ys)

    flags = None
    if quality is not None:
        with rasterio.open(quality[0]) as qsrc:
            flags = qsrc.read(1)
    keep = quality[1] if quality else frozenset()

    out: dict[str, float] = {}
    rejected = 0
    for cell_id, r, c in zip(cell_ids, rows, cols, strict=True):
        if not (0 <= r < band.shape[0] and 0 <= c < band.shape[1]):
            continue
        value = band[r, c]
        if np.ma.is_masked(value):
            continue
        if flags is not None and int(flags[r, c]) not in keep:
            rejected += 1
            continue
        out[cell_id] = float(value)
    return out, rejected


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("rasters", nargs="+", help="Granules (.h5) or GeoTIFFs. Globs are accepted.")
    p.add_argument("--band", type=int, default=1, help="Band index, for GeoTIFF input")
    args = p.parse_args()

    # Expand globs here as well as relying on the shell: PowerShell does not
    # expand them for a native executable, so a pattern that works under bash
    # would otherwise arrive as a literal filename and fail confusingly.
    paths: list[Path] = []
    for pattern in args.rasters:
        matches = [Path(m) for m in globlib.glob(pattern)]
        paths.extend(matches or [Path(pattern)])

    missing = [p for p in paths if not p.exists()]
    if missing:
        for m in missing:
            print(f"Not found: {m}")
        print("\nFetch granules with: python scripts/download_viirs.py")
        return 1

    paths = sorted(set(paths))
    grid = get_grid()
    cell_ids = grid.all_cell_ids()
    lats, lons = zip(*(grid.cell_center(c) for c in cell_ids), strict=True)

    print(f"Sampling {len(cell_ids):,} cell centres from {len(paths)} granule(s)\n")
    if len(paths) == 1:
        print("  Only one granule — cloud and moonlight artefacts will survive into")
        print("  the layer as fake dark patches. Prefer a dozen nights.\n")

    per_cell: dict[str, list[float]] = {}
    used = 0
    total_rejected = 0

    for path in paths:
        try:
            source, quality = resolve_source(path)
            values, rejected = sample(source, list(cell_ids), lats, lons, args.band, quality)
        except ImportError:
            print("rasterio is not installed. It is an optional extra:")
            print('  uv pip install -e ".[raster]"')
            return 1
        except Exception as exc:  # noqa: BLE001 - one bad granule must not lose the rest
            print(f"  skip {path.name}: {exc}")
            continue

        if not values:
            if rejected:
                # Monsoon months over Delhi routinely lose every pixel here.
                print(f"  skip {path.name}: all {rejected:,} cells failed the quality flag (cloud)")
            else:
                print(f"  skip {path.name}: no cells sampled — does it cover Delhi?")
            continue

        for cell_id, v in values.items():
            per_cell.setdefault(cell_id, []).append(v)
        used += 1
        total_rejected += rejected
        dropped = f", {rejected:,} dropped on quality flag" if quality else ""
        print(f"  {path.name}  ->  {len(values):,} cells{dropped}")

    if not per_cell:
        print("\nNo cells sampled from any granule. Check that these cover Delhi.")
        return 1

    raw = {cell_id: statistics.median(vs) for cell_id, vs in per_cell.items()}
    values = normalise(raw, invert=False)

    layer = NTLSLayer()
    layer.save(
        values,
        meta={
            "source": f"{used} granule(s), per-cell median",
            "granules": [p.name for p in paths[:20]],
            "granule_count": used,
            "cells": len(values),
            "quality_rejected_samples": total_rejected,
        },
    )

    depth = statistics.median(len(v) for v in per_cell.values())
    print(f"\nWrote {layer.path}")
    print(f"  {len(values):,} of {grid.n_cells:,} cells, median {depth:.0f} granule(s) per cell")
    print(f"  raw radiance range: {min(raw.values()):.2f} .. {max(raw.values()):.2f}")
    if used == 1:
        print("\n  Built from a single night — see the warning above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
