# Data directory

**The files the running app reads are committed**, so a fresh clone works
without running anything; they are marked below. Everything else stays out of
git. All of it is reproducible from [`../../scripts/`](../../scripts/) — with one
exception that matters.

Re-run a script only to refresh its data. Each refresh adds its whole file to the
repo's history (8 MB for the roads), so refresh occasionally, not on a schedule.
OpenStreetMap data is ODbL and the population data CC BY 4.0: keep the credits in
each file's `source` field and on the map.

```
raw/         Downloads: crime CSVs, VIIRS rasters, OSM extracts, district GeoJSON
processed/   Built artefacts the app reads at startup
archive/     The news archive. NOT reproducible — see archive/README.md
```

`raw/` can be re-downloaded and `processed/` rebuilt from it. `archive/`
accumulates one day at a time from news that has since rotated off the feeds;
losing it restarts a clock measured in months. Back it up.

## What lands in `processed/`

| File | Built by | Read by | In git |
|---|---|---|---|
| `grid.json` | `build_grid.py` | Everything — it pins the cell ids | Yes |
| `map_pois.json` | `fetch_map_pois.py` | Map, place search, open places, help | Yes |
| `roads_major.json` | `fetch_roads.py` | Road-type term | Yes |
| `cameras.json` | `fetch_cameras.py` | Route facts | Yes |
| `signals.json` | `fetch_signals.py` | Route facts | Yes |
| `ntls.json` | `ingest_viirs.py` | NTLS layer | Yes |
| `cds.json` | `build_cds.py` | CDS layer | Yes |
| `cip.json` | `ingest_crime_data.py` | CIP layer | No |
| `crime_incidents.json` | `ingest_crime_data.py` | `build_trc.py` (intermediate) | No |
| `trc_hourly.json` | `build_trc.py` | TRC layer | No |
| `nsi_incidents.json` | `run_news_agent.py` | NSI layer (live 30-day window) | No, stale within days |
| `geocode_cache.json` | news agent | News agent (saves repeat geocoding calls) | No |
| `article_cache.json` | news agent | News agent (saves refetching articles) | No |
| `cell_districts.json` | `build_grid.py --districts` | Reporting only | No |

`raw/population_cells.json` (`ingest_population.py`) is also committed: the app
reads it for isolated stretches, and `build_cds.py` for the crowd layer.

## What lands in `archive/`

| File | Built by | Read by |
|---|---|---|
| `nsi_archive.jsonl` | `run_news_agent.py` (every run, append-only) | `export_archive.py` → the CIP/TRC pipeline |

An empty directory is fine. Missing layers score `NEUTRAL` and report
`available: false` on `/health` — the app runs either way.

## If you rebuild the grid

Cell ids are only meaningful relative to the bbox and cell size in `grid.json`.
Changing either means **every other file here points at the wrong places on the
map**, silently. `build_grid.py` refuses to overwrite a mismatched manifest
without `--force`; if you force it, delete everything else in `processed/` and
rebuild all five layers.
