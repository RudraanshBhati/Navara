"""Permanent, append-only record of every incident the agent has ever seen.

WHY THIS EXISTS, SEPARATELY FROM THE NSI LAYER
----------------------------------------------
The NSI layer is a *window*. `node_merge` drops anything older than
``news_max_age_days`` so the map reflects what happened recently, and that is
correct for what NSI does — a stabbing three days ago should weigh more than the
same stabbing last month.

But it means the agent was throwing away the only incident-level Delhi crime
data this project can obtain. Every layer needs a source; CIP's (a crime dataset
with per-incident coordinates *and* timestamps) does not exist publicly in usable
form (see docs/DATA_SOURCES.md). What the agent produces each day — located,
timestamped, categorised incidents — is exactly the shape CIP and TRC need. It
is far too sparse on any single day, which is why RESEARCH.md is right that news
is not a substitute for CIP *today*. Accumulated over months, it stops being a
single day.

So: NSI keeps its decaying window, and everything that passes through the window
is also written here, permanently. The two have different jobs and different
retention, and conflating them is what made the data disappear.

FORMAT — JSON Lines, append-only, last-write-wins
-------------------------------------------------
One JSON object per line. Appending never rewrites earlier bytes, so a crash or
a full disk mid-write costs at most the last line rather than the whole history
— which matters for a file that takes months of calendar time to rebuild and
cannot be re-downloaded.

An incident may legitimately be written more than once: re-extraction can
improve a record (a better date read out of the article body, a confidence
revision). Readers fold the log and take the **last** record for each id.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from ..config import ARCHIVE_DIR
from ..data.nsi import Incident

log = logging.getLogger(__name__)

ARCHIVE_PATH = ARCHIVE_DIR / "nsi_archive.jsonl"

#: Every article the extractor read, with what it decided — see append_reviews.
REVIEW_LOG_PATH = ARCHIVE_DIR / "news_reviewed.jsonl"

#: One line per agent run — see append_run.
RUN_LOG_PATH = ARCHIVE_DIR / "agent_runs.jsonl"

#: Excluded when deciding whether a record has changed. `cell_id` is derived
#: from lat/lon by NSILayer._build_index, which mutates the incident in place —
#: so a stored incident has it set and a freshly geocoded one does not, and
#: comparing it would rewrite every incident on every run forever.
_VOLATILE_FIELDS = frozenset({"cell_id"})


def _fingerprint(record: dict) -> tuple:
    return tuple(sorted((k, _hashable(v)) for k, v in record.items() if k not in _VOLATILE_FIELDS))


def _hashable(value):
    return tuple(value) if isinstance(value, list) else value


def load_archive(path: Path | None = None) -> dict[str, Incident]:
    """Fold the log into id -> most recent record.

    A malformed line is skipped rather than fatal. This file is appended to by a
    scheduled job for months on end; one bad line from an interrupted write
    should not make the entire history unreadable.
    """
    path = path or ARCHIVE_PATH
    if not path.exists():
        return {}

    out: dict[str, Incident] = {}
    malformed = 0

    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                out[record["id"]] = Incident(**record)
            except (json.JSONDecodeError, KeyError, TypeError):
                malformed += 1

    if malformed:
        log.warning("archive: skipped %d malformed line(s) in %s", malformed, path)
    return out


def append_incidents(incidents: list[Incident], path: Path | None = None) -> dict:
    """Append incidents that are new, or whose content has changed.

    Idempotent: running the agent twice in one day appends nothing the second
    time. Without that, every incident would be rewritten on every run for as
    long as it stayed inside the live window, inflating the log roughly 30x.
    """
    path = path or ARCHIVE_PATH
    if not incidents:
        return {"archived_new": 0, "archived_updated": 0, "archive_total": len(load_archive(path))}

    known = load_archive(path)
    seen = {inc_id: _fingerprint(asdict(inc)) for inc_id, inc in known.items()}

    new_lines: list[str] = []
    added = updated = 0

    for inc in incidents:
        record = asdict(inc)
        fp = _fingerprint(record)
        if inc.id not in seen:
            added += 1
        elif seen[inc.id] != fp:
            updated += 1
        else:
            continue
        seen[inc.id] = fp
        new_lines.append(json.dumps(record, sort_keys=True))

    if new_lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + "\n")

    total = len(seen)
    log.info(
        "archive: +%d new, %d updated -> %d incidents all-time (%s)",
        added,
        updated,
        total,
        path,
    )
    return {"archived_new": added, "archived_updated": updated, "archive_total": total}


def append_reviews(reviews: list[dict], path: Path | None = None) -> dict:
    """Record every article the extractor read, and what became of it.

    The incident archive only holds what survived every stage: a safety story
    whose location was a district, or that the geocoder could not place, or
    that the model failed to parse, left no trace at all. Those are exactly the
    records worth re-examining once a better extractor or geocoder is wired up,
    and by then the article has rotated off the feed. So the review log keeps
    one row per article URL with its outcome and the text that was read.

    Same append-only, last-write-wins shape as the incident archive, keyed by
    URL. A row is rewritten only when its outcome or extraction changes, so a
    story re-read on every run inside the lookback window is logged once.
    """
    path = path or REVIEW_LOG_PATH
    if not reviews:
        return {"reviewed_new": 0, "reviewed_updated": 0}

    known = _fold_by(path, "url")
    new_lines: list[str] = []
    added = updated = 0

    for review in reviews:
        fp = _fingerprint({k: v for k, v in review.items() if k != "reviewed_at"})
        prev = known.get(review["url"])
        if prev is None:
            added += 1
        elif _fingerprint({k: v for k, v in prev.items() if k != "reviewed_at"}) != fp:
            updated += 1
        else:
            continue
        known[review["url"]] = review
        new_lines.append(json.dumps(review, sort_keys=True, default=str))

    _append_lines(path, new_lines)
    return {"reviewed_new": added, "reviewed_updated": updated}


def append_run(record: dict, path: Path | None = None) -> None:
    """One line per run: when it ran, what each stage produced, what broke.

    This is how a gap becomes visible. The incident archive cannot tell "no
    incidents this week" from "the agent did not run this week", and only the
    first is a quiet news week — the second is data that is gone.
    """
    _append_lines(path or RUN_LOG_PATH, [json.dumps(record, sort_keys=True, default=str)])


def load_runs(path: Path | None = None) -> list[dict]:
    path = path or RUN_LOG_PATH
    if not path.exists():
        return []
    runs = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                runs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return runs


def _fold_by(path: Path, key: str) -> dict[str, dict]:
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                record = json.loads(line)
                out[record[key]] = record
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return out


def _append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def archive_stats(path: Path | None = None) -> dict:
    """Summary of what has accumulated — how CIP readiness gets tracked.

    ``days_covered`` is the span from the earliest to the latest incident, not
    the number of days the agent ran. A gap in the middle shows up as coverage
    it does not actually have, so treat it as an upper bound.
    """
    records = load_archive(path)
    if not records:
        return {"incidents": 0, "days_covered": 0, "earliest": None, "latest": None, "cells": 0}

    dates = sorted(inc.occurred_dt for inc in records.values())
    cells = {inc.cell_id for inc in records.values() if inc.cell_id}

    return {
        "incidents": len(records),
        "days_covered": (dates[-1] - dates[0]).days + 1,
        "earliest": dates[0].isoformat(),
        "latest": dates[-1].isoformat(),
        "cells": len(cells),
    }
