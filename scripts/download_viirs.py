"""Download VIIRS VNP46A2 night-lights granules covering Delhi.

    python scripts/download_viirs.py                # 10 most recent nights
    python scripts/download_viirs.py --nights 20

Needs EARTHDATA_TOKEN in backend/.env — free and instant from
https://urs.earthdata.nasa.gov/ (profile -> Generate Token).

WHY MORE THAN ONE NIGHT
-----------------------
A single night's raster carries cloud shadow and moonlight variation. Those
show up as dark patches that are indistinguishable from an unlit street, and a
fake dark patch here becomes a "poorly lit stretch" warning on a route someone
is deciding whether to walk at 11pm. `ingest_viirs.py` takes the per-cell median
across every granule you pass it, which is what makes that transient rather than
permanent. Ten nights is a reasonable floor.

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
SHORT_NAME = "VNP46A2"

MIN_LON, MIN_LAT, MAX_LON, MAX_LAT = DELHI_BBOX


def find_granules(nights: int) -> list[tuple[str, str]]:
    """(filename, url) for the most recent granules covering Delhi."""
    params = {
        "short_name": SHORT_NAME,
        "bounding_box": f"{MIN_LON},{MIN_LAT},{MAX_LON},{MAX_LAT}",
        "page_size": str(nights),
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
    p.add_argument("--nights", type=int, default=10, help="How many recent nights (default: 10)")
    p.add_argument("--out", type=Path, default=RAW_DIR, help="Where to write granules")
    args = p.parse_args()

    token = get_settings().earthdata_token.strip()
    if not token:
        print("EARTHDATA_TOKEN is not set. Add it to backend/.env.")
        print("Free and instant: https://urs.earthdata.nasa.gov/ -> Generate Token")
        return 1

    print(f"Searching CMR for {SHORT_NAME} over Delhi ({MIN_LON},{MIN_LAT},{MAX_LON},{MAX_LAT})")
    granules = find_granules(args.nights)
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

    have = sorted(args.out.glob(f"{SHORT_NAME}*.h5"))
    print(f"\n{fetched} downloaded, {len(have)} granules on disk in {args.out}")
    if have:
        print("\nNext:")
        print(f"  python scripts/ingest_viirs.py {args.out}/{SHORT_NAME}*.h5")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
