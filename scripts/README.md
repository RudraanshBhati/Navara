# Scripts

Run from the repo root with the backend venv:

```bash
backend/.venv/Scripts/python.exe scripts/<name>.py    # Windows
backend/.venv/bin/python scripts/<name>.py            # macOS / Linux
```

Every script takes `--help`.

## Order to run them

```bash
# 1. Pin the grid. Everything else is keyed to this.
python scripts/build_grid.py

# 2. Crime → CIP, and the timestamp cache TRC needs.
python scripts/ingest_crime_data.py backend/data/raw/delhi_crime.csv

# 3. TRC, from the cache step 2 wrote.
python scripts/build_trc.py

# 4. Lighting. Needs the raster and the `raster` extra.
uv pip install -e "backend[raster]"
python scripts/ingest_viirs.py backend/data/raw/delhi_viirs.tif

# 5. Crowd proxy. Queries Overpass, no key.
python scripts/ingest_osm_poi.py

# 6. News. Run on a schedule thereafter.
python scripts/run_news_agent.py
```

Step 2 is the one with no public dataset behind it. Until that is solved, the
news agent doubles as the way to build one — every run appends to a permanent
archive, and `export_archive.py` turns that archive into the CSV step 2 wants:

```bash
python scripts/export_archive.py --stats      # how much has accumulated
python scripts/export_archive.py backend/data/raw/news_derived_crime.csv
```

That is a months-long loop, not an afternoon. Start the agent running early —
it is the only part of this project gated on elapsed time rather than effort.

Check progress at any point with `GET /health`, or `POST /admin/reload` to make
a running server pick up newly built layers without a restart.

## What each one needs

| Script | Input | Keys | Writes |
|---|---|---|---|
| `build_grid.py` | — (district GeoJSON optional) | none | `grid.json`, `cell_districts.json` |
| `ingest_crime_data.py` | incident CSV with coords **and timestamps** | none | `cip.json`, `crime_incidents.json` |
| `build_trc.py` | `crime_incidents.json` | none | `trc_hourly.json` |
| `ingest_viirs.py` | VNP46A2 GeoTIFF | none (Earthdata to download) | `ntls.json` |
| `ingest_osm_poi.py` | — (queries Overpass) | none | `cds.json` |
| `run_news_agent.py` | — (fetches news) | `ANTHROPIC_API_KEY`, `GOOGLE_MAPS_API_KEY` | `nsi_incidents.json`, `nsi_archive.jsonl` |
| `export_archive.py` | `nsi_archive.jsonl` | none | a crime CSV for `ingest_crime_data.py` |

Everything lands in `backend/data/processed/` and is reproducible from here —
**except `backend/data/archive/`**, which accumulates day by day and cannot be
rebuilt. See [`backend/data/archive/README.md`](../backend/data/archive/README.md).

See [`docs/DATA_SOURCES.md`](../docs/DATA_SOURCES.md) for where to get the
inputs, and in particular why district-level annual crime totals will not work.

## Trying things without keys or data

```bash
python scripts/run_news_agent.py --offline --dry-run
```

Runs the whole agent graph on canned articles, writing nothing. Useful for
checking the wiring.
