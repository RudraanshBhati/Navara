"""Run the LangGraph news agent and refresh the NSI layer.

    uv run python scripts/run_news_agent.py                 # normal run
    uv run python scripts/run_news_agent.py --dry-run       # fetch + extract, write nothing
    uv run python scripts/run_news_agent.py --lookback 72   # look back further
    uv run python scripts/run_news_agent.py --offline       # no network, no LLM

Needs ANTHROPIC_API_KEY (extraction) and GOOGLE_MAPS_API_KEY (geocoding) in
backend/.env. `--offline` skips both by using canned articles — useful for
checking the graph wiring without spending anything.

Run this on a schedule once the pipeline is real. Every few hours is plenty:
the halflife is a week, so the layer does not change fast, and each run costs
one LLM call per article that clears the prefilter.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.agent.graph import run_agent  # noqa: E402
from app.agent.sources import Article, NewsSource  # noqa: E402


class FixtureSource(NewsSource):
    """Canned articles so the graph can be exercised without network or keys."""

    name = "fixture"

    def fetch(self, lookback_hours: int) -> list[Article]:  # noqa: ARG002
        now = datetime.now(timezone.utc)
        return [
            Article(
                title="Woman robbed at knifepoint near Saket Metro station",
                url="https://example.test/a1",
                source="fixture",
                published_at=now - timedelta(hours=6),
                summary=(
                    "A 27-year-old woman was robbed at knifepoint late on Tuesday "
                    "night while walking from Saket Metro station towards Pushp "
                    "Vihar. Police have registered an FIR."
                ),
            ),
            Article(
                title="Chain snatching reported in Karol Bagh market area",
                url="https://example.test/a2",
                source="fixture",
                published_at=now - timedelta(hours=20),
                summary=(
                    "Two men on a motorcycle snatched a gold chain from a pedestrian "
                    "near Ajmal Khan Road in Karol Bagh on Monday evening."
                ),
            ),
            Article(
                title="Delhi Police announce new traffic plan for Republic Day",
                url="https://example.test/a3",
                source="fixture",
                published_at=now - timedelta(hours=3),
                summary="Traffic advisory issued for central Delhi. Not a safety incident.",
            ),
        ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lookback", type=int, default=None, help="Hours to look back")
    parser.add_argument("--dry-run", action="store_true", help="Do not write the layer")
    parser.add_argument("--offline", action="store_true", help="Use canned articles")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s | %(message)s",
    )

    sources = [FixtureSource()] if args.offline else None
    state = run_agent(lookback_hours=args.lookback, dry_run=args.dry_run, sources=sources)

    print("\n--- run summary ---")
    print(json.dumps(state.get("stats", {}), indent=2, default=str))

    errors = state.get("errors", [])
    if errors:
        print("\nerrors:")
        for e in errors:
            print(f"  - {e}")

    incidents = state.get("incidents", [])
    if incidents:
        print(f"\n{len(incidents)} live incidents, most recent first:")
        for inc in incidents[:10]:
            print(
                f"  {inc.occurred_at[:10]}  conf={inc.confidence:<5.2f} "
                f"sev={inc.severity:<4.2f} {inc.category:<16} {inc.headline[:60]}"
            )
    else:
        print("\nNo live incidents. Either nothing was found, or extraction was skipped.")

    # A run that fetched nothing at all almost always means a source broke, not
    # that Delhi had a quiet day. Fail loudly so a cron job surfaces it.
    if not args.offline and state.get("stats", {}).get("fetched", 0) == 0:
        print("\nNo articles fetched from any source — check network and feed URLs.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
