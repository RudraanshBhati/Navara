"""Export the news archive as a crime CSV that ingest_crime_data.py can read.

    python scripts/export_archive.py --stats
    python scripts/export_archive.py backend/data/raw/news_derived_crime.csv

This closes the loop the archive exists for:

    run_news_agent.py  ->  nsi_archive.jsonl  ->  export_archive.py
                                                       |
                                                       v
                        cip.json  <-  ingest_crime_data.py  <-  CSV
                                             |
                                             v
                                      crime_incidents.json -> build_trc.py

Column names are chosen to match `ingest_crime_data.py`'s first guess for each
field, so no --lat-col flags are needed.

DO NOT RUN THIS YET, in the sense that matters
----------------------------------------------
A CIP layer built from two weeks of news is worse than no CIP layer: it is
sparse enough to be noise, and unlike a missing layer it will not announce
itself — `/health` reports `available: true` and the model starts making
confident claims from a handful of incidents. `--stats` exists so the decision
to build CIP is made against a number rather than an impatience. See
MIN_USEFUL_* below for what that number should roughly be.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.agent.archive import ARCHIVE_PATH, archive_stats, load_archive  # noqa: E402

#: Below this the export runs but warns. Not a hard rule — a threshold nobody
#: can justify is worse than none — but the reasoning behind the order of
#: magnitude: CIP is a density surface over ~10,700 cells, TRC wants 30+
#: incidents in a single cell before it trusts that cell's own hourly curve
#: (see app/data/trc.py), and Delhi news yields single-digit located incidents
#: on a normal day. Six months is the point where the citywide picture starts
#: to hold shape; cell-level curves need considerably longer.
MIN_USEFUL_INCIDENTS = 1_500
MIN_USEFUL_DAYS = 180

#: Extractions the model was unsure of are fine for NSI, where they are scaled
#: by confidence and decay out in a week. Baked into a permanent historical
#: surface they are not — nothing downstream of the CSV carries the confidence
#: through, so a 0.3-confidence guess would count exactly as much as a
#: 0.95-confidence report.
DEFAULT_MIN_CONFIDENCE = 0.5


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("out", type=Path, nargs="?", help="CSV to write")
    p.add_argument("--archive", type=Path, default=ARCHIVE_PATH)
    p.add_argument("--stats", action="store_true", help="Report accumulation and exit")
    p.add_argument(
        "--min-confidence",
        type=float,
        default=DEFAULT_MIN_CONFIDENCE,
        help=f"Drop incidents below this confidence (default: {DEFAULT_MIN_CONFIDENCE})",
    )
    args = p.parse_args()

    if not args.archive.exists():
        print(f"No archive at {args.archive}")
        print("Nothing has accumulated yet. Run scripts/run_news_agent.py on a schedule.")
        return 1

    stats = archive_stats(args.archive)
    print(f"Archive: {args.archive}")
    print(f"  {stats['incidents']:,} incidents across {stats['cells']:,} cells")
    print(f"  {stats['earliest'][:10]} .. {stats['latest'][:10]} ({stats['days_covered']} days)")

    thin = stats["incidents"] < MIN_USEFUL_INCIDENTS or stats["days_covered"] < MIN_USEFUL_DAYS
    if thin:
        print(
            f"\n  Thin: a useful CIP surface wants roughly {MIN_USEFUL_INCIDENTS:,}+ incidents\n"
            f"  over {MIN_USEFUL_DAYS}+ days. Building CIP from this will produce a layer that\n"
            "  reports available=true and is mostly noise. Keep accumulating."
        )

    if args.stats or args.out is None:
        return 0

    records = load_archive(args.archive)
    kept = [i for i in records.values() if i.confidence >= args.min_confidence]
    dropped = len(records) - len(kept)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["latitude", "longitude", "datetime", "crime_type", "confidence", "url"])
        for inc in sorted(kept, key=lambda i: i.occurred_at):
            writer.writerow(
                [inc.lat, inc.lon, inc.occurred_at, inc.category, inc.confidence, inc.url]
            )

    print(f"\nWrote {args.out} ({len(kept):,} rows, {dropped:,} below confidence threshold)")
    print("\nNext:")
    print(f"  python scripts/ingest_crime_data.py {args.out}")
    print("  python scripts/build_trc.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
