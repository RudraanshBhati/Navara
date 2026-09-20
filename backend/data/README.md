# Data directory

**Nothing here is committed**, and all of it is reproducible from
[`../../scripts/`](../../scripts/) — with one exception that matters.

```
raw/         Downloads: crime CSVs, VIIRS rasters, OSM extracts, district GeoJSON
processed/   Built artefacts the app reads at startup
archive/     The news archive. NOT reproducible — see archive/README.md
```

`raw/` can be re-downloaded and `processed/` rebuilt from it. `archive/`
accumulates one day at a time from news that has since rotated off the feeds;
losing it restarts a clock measured in months. Back it up.

## What lands in `processed/`

| File | Built by | Read by |
|---|---|---|
| `grid.json` | `build_grid.py` | Everything — it pins the cell ids |
| `cip.json` | `ingest_crime_data.py` | CIP layer |
| `crime_incidents.json` | `ingest_crime_data.py` | `build_trc.py` (intermediate) |
| `trc_hourly.json` | `build_trc.py` | TRC layer |
| `ntls.json` | `ingest_viirs.py` | NTLS layer |
| `cds.json` | `ingest_osm_poi.py` | CDS layer |
| `nsi_incidents.json` | `run_news_agent.py` | NSI layer (live 30-day window) |
| `geocode_cache.json` | news agent | News agent (saves repeat geocoding calls) |
| `cell_districts.json` | `build_grid.py --districts` | Reporting only |

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
