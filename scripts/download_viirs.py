"""Download VIIRS Black Marble night-lights granules covering Delhi.

    python scripts/download_viirs.py                          # 12 most recent months
    python scripts/download_viirs.py --product VNP46A2 --count 20   # 20 recent nights

Needs EARTHDATA_TOKEN in backend/.env — free and instant from
https://urs.earthdata.nasa.gov/ (profile -> Generate Token).

MONTHLY COMPOSITES BY DEFAULT
-----------------------------
VNP46A3 is NASA's own monthly composite of the nightly product, with a
per-pixel quality flag saying whether more than three clear nights went into
it or whether the value was gap-filled from history. A year of those, with the
flagged pixels dropped, is a better lighting surface than any run of nightly
granules: a dozen consecutive nights can fall entirely inside the monsoon,
where most of what you get is interpolation over cloud. ~110-150 MB a month.

VNP46A2 (nightly) remains available. If you use it, take many nights:
`ingest_viirs.py` takes the per-cell median across every granule, which is what
stops one night's cloud shadow becoming a permanent "poorly lit stretch".

WHICH TILE
----------
Delhi falls in h25v06. Rather than hardcode that, this queries CMR by bounding
box, so the tile is derived from DELHI_BBOX and stays correct if the bbox moves.
(The tile naming is easy to get wrong by hand: tiles are 10 degrees square, so
h = (lon + 180) / 10 puts Delhi's 77.2E in h25, not the h26 that covers
80-90E.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import DELHI_BBOX, RAW_DIR, get_settings  # noqa: E402

CMR_URL = "https://cmr.earthdata.nasa.gov/search/granules.json"
PRODUCTS = ("VNP46A3", "VNP46A2")

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = DELHI_BBOX


def find_granules(product: str, count: int) -> list[tuple[str, str]]:
    """(filename, url) for the most recent granules covering Delhi."""
    params = {
        "short_name": product,
        "bounding_box": f"{MIN_LON},{MIN_LAT},{MAX_LON},{MAX_LAT}",
        "page_size": str(count),
        "sort_key": "-start_date",
    }
    r = httpx.get(CMR_URL, params=params, timeout=60.0)
    r.raise_for_status()

    out: list[tuple[str, str]] = []
    for entry in r.json().get("feed", {}).get("entry", []):
        urls = [
            link["href"]
            for link in entry.get("links", [])
            if link.get("href", "").endswith(".h5")
        ]
        if urls:
            out.append((urls[0].rsplit("/", 1)[-1], urls[0]))
    return out


def download(url: str, dest: Path, token: str) -> bool:
    """Stream one granule to disk. Returns False if it was already there."""
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  have   {dest.name}")
        return False

    # Download to a temp name and rename on success, so an interrupted run
    # cannot leave a truncated file that the next run treats as complete.
    tmp = dest.with_suffix(dest.suffix + ".part")
    with httpx.stream(
        "GET",
        url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=600.0,
        follow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 20):
                fh.write(chunk)
    tmp.replace(dest)
    print(f"  got    {dest.name}  ({dest.stat().st_size / 1e6:.1f} MB)")
    return True


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--product", choices=PRODUCTS, default="VNP46A3", help="Monthly (default) or nightly")
    p.add_argument(
        "--count",
        "--nights",
        type=int,
        default=12,
        help="How many recent granules: months for VNP46A3, nights for VNP46A2 (default: 12)",
    )
    p.add_argument("--out", type=Path, default=RAW_DIR, help="Where to write granules")
    args = p.parse_args()
    product = args.product

    token = get_settings().earthdata_token.strip()
    if not token:
        print("EARTHDATA_TOKEN is not set. Add it to backend/.env.")
        print("Free and instant: https://urs.earthdata.nasa.gov/ -> Generate Token")
        return 1

    print(f"Searching CMR for {product} over Delhi ({MIN_LON},{MIN_LAT},{MAX_LON},{MAX_LAT})")
    granules = find_granules(product, args.count)
    if not granules:
        print("No granules found. Check the bbox and that CMR is reachable.")
        return 1

    print(f"{len(granules)} granules:\n")
    args.out.mkdir(parents=True, exist_ok=True)
    fetched = 0

    for name, url in granules:
        try:
            if download(url, args.out / name, token):
                fetched += 1
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            print(f"  FAILED {name}: HTTP {code}")
            if code in (401, 403):
                print("\n  Authentication rejected. Earthdata tokens expire after 60 days —")
                print("  regenerate at https://urs.earthdata.nasa.gov/ and update backend/.env.")
                return 1
        except httpx.HTTPError as exc:
            print(f"  FAILED {name}: {exc}")

    have = sorted(args.out.glob(f"{product}*.h5"))
    print(f"\n{fetched} downloaded, {len(have)} {product} granules on disk in {args.out}")
    if have:
        print("\nNext:")
        print(f"  python scripts/ingest_viirs.py {args.out}/{product}*.h5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
