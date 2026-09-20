# The news archive

**This is the only directory in the project that cannot be regenerated. Back it
up.**

Everything in `../raw/` can be re-downloaded and everything in `../processed/`
can be rebuilt from it. This cannot. `nsi_archive.jsonl` accumulates one day at
a time from whatever the news agent saw that day, and by the time you notice it
is missing, those articles have rotated off the RSS feeds and out of GDELT's
practical reach. A deleted archive is a restarted clock, measured in months.

## What it is

An append-only JSON Lines log — one incident per line, written by
`app/agent/archive.py` at the end of every agent run. Readers fold the log and
take the last record for each `id`, so an incident re-extracted with better data
appears more than once and the newest wins.

## Why it exists

The NSI layer is a 30-day decaying window, which is correct for what NSI does:
a stabbing last night should outweigh the same stabbing last month. But it meant
the agent was discarding the only incident-level Delhi crime data this project
can get — located, timestamped, categorised — and that is exactly what CIP and
TRC need as input.

Delhi publishes no public crime dataset with per-incident coordinates *and*
timestamps (see `../../../docs/DATA_SOURCES.md`). So NAVARA accumulates one.
News on any single day is far too sparse to be a crime surface; a year of it
is a different object.

## Checking on it

```bash
python scripts/export_archive.py --stats
```

Reports incidents, distinct cells, and the span covered. When that is deep
enough to be worth building on — roughly 1,500+ incidents over 180+ days, and
the script says so — export it and run the normal CIP pipeline:

```bash
python scripts/export_archive.py backend/data/raw/news_derived_crime.csv
python scripts/ingest_crime_data.py backend/data/raw/news_derived_crime.csv
python scripts/build_trc.py
```

## Backing it up

It is gitignored: it grows without bound and carries article URLs, so it does
not belong in the repo. Anything durable works — an object-store sync, a
scheduled copy alongside the cron job that runs the agent. What matters is that
it is somewhere other than one laptop.
