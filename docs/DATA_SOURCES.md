# Data sources — where each layer comes from

Nothing in `backend/data/` is committed; it is all reproducible from the scripts
in [`scripts/`](../scripts/). This document says what to download and what to
watch out for.

---

## The thing to get right first: what the crime data must contain

This determines whether two of the five layers can exist at all.

| We need | Why |
|---|---|
| **Coordinates per incident** | Required for CIP. District-level annual totals cannot be gridded — spreading a district's total across every cell in it produces 15 distinct values over ~10,700 cells, which is not a spatial signal |
| **Timestamps per incident** | Required for TRC. No per-incident times means no crime-by-hour histogram, which means the TRC layer cannot be built. Not approximated — not built |
| **Offence type** | Optional but valuable. Drives severity weighting; without it a pickpocketing counts the same as an assault |

**NCRB and data.gov.in publish district-wise annual counts.** Those satisfy none
of the first two. They are useful for cross-checking magnitudes and for a
district-level sanity view, but they cannot build CIP or TRC.

What you want is an **incident-level** dataset. Options, in order of preference:

1. **Delhi Police FIR data** — the real thing, if obtainable. Coordinates are
   often absent and need geocoding from the police-station field.
2. **Kaggle Delhi crime datasets** — several exist with varying quality. Check
   for coordinate and timestamp columns *before* building anything on one.
3. **Geocoded news incidents** — what our own NSI agent produces. Not a
   substitute for CIP (far too sparse), but it is real, located, and timestamped.

Then:

```bash
uv run python scripts/ingest_crime_data.py backend/data/raw/<file>.csv \
    --lat-col latitude --lon-col longitude \
    --time-col datetime --offence-col crime_type
uv run python scripts/build_trc.py
```

The ingest script guesses column names from common variants and tells you what
it picked. It also warns explicitly when a dataset has no timestamps.

---

## Lighting (NTLS) — VIIRS night radiance

Product: **VNP46A2** ("Black Marble", gap-filled BRDF-corrected night-time
radiance), 500 m native resolution — which matches our cell size exactly, so no
resampling judgement is needed.

Two routes:

| Source | Access | Notes |
|---|---|---|
| [NASA Black Marble](https://blackmarble.gsfc.nasa.gov/) | Free [Earthdata login](https://urs.earthdata.nasa.gov/), instant | **Recommended.** Grab tile `h26v06`, clip to Delhi. No GEE dependency |
| [Google Earth Engine](https://signup.earthengine.google.com) | Account approval, 1–2 days | Asset `NASA/VIIRS/002/VNP46A2`, band `Gap_Filled_DNB_BRDF_Corrected_NTL`. Better for compositing many nights server-side |

**Use a multi-night median composite, not a single night.** One night's raster
carries cloud artefacts and moonlight variation that show up as fake dark
patches — and a fake dark patch becomes a "poorly lit stretch" warning on
someone's route.

```bash
uv pip install -e ".[raster]"       # rasterio is an optional extra
uv run python scripts/ingest_viirs.py backend/data/raw/delhi_viirs.tif
```

---

## Crowd proxy (CDS) — OpenStreetMap POIs

Queried live from the [Overpass API](https://overpass-api.de/) — free, no key.
Cached to `backend/data/raw/delhi_pois.json` on first run; Overpass is a shared
volunteer service, so don't hammer it.

```bash
uv run python scripts/ingest_osm_poi.py
```

Read the caveats in [`app/data/cds.py`](../backend/app/data/cds.py) before
trusting the output. Low POI density means "quiet area" *or* "nobody has mapped
it", and nothing in the data distinguishes them.

---

## News (NSI) — the agent

| Source | Key | Notes |
|---|---|---|
| [GDELT 2.0 DOC API](https://api.gdeltproject.org/api/v2/doc/doc) | None | The workhorse. 15-minute update cadence, global index |
| Delhi city-desk RSS | None | Higher local signal density, but only ~50 items of history |
| [NewsAPI](https://newsapi.org/) | Optional | Better metadata; free tier delays articles 24h, which blunts the point of this layer. Off unless `NEWSAPI_KEY` is set |

Extraction needs `ANTHROPIC_API_KEY`; geocoding needs `GOOGLE_MAPS_API_KEY`
(Geocoding API enabled).

```bash
uv run python scripts/run_news_agent.py --offline --dry-run   # no keys, canned data
uv run python scripts/run_news_agent.py                       # the real thing
```

Feed URLs rot. If a run reports zero articles fetched, check
[`app/agent/sources.py`](../backend/app/agent/sources.py) before assuming Delhi
had a quiet day — the runner exits non-zero on a total fetch failure precisely
so a cron job surfaces this.

---

## District boundaries — optional

Only needed for reporting and for joining district-keyed data. Nothing in the
scoring model uses it.

- [`datameet/maps`](https://github.com/datameet/maps) — community scrape, the usual source
- [data.gov.in](https://data.gov.in/) — official, cross-check against it

```bash
uv run python scripts/build_grid.py --districts backend/data/raw/delhi_districts.geojson
```

---

## Validation: Safetipin

Not an ingested layer — a **ground truth set** for tuning the CSS weights.
Safetipin has audited Delhi on nine parameters and published the results.
Where our model disagrees with theirs on a neighbourhood, that is a bug signal.
See [`RESEARCH.md § Ground truth`](RESEARCH.md#ground-truth-safetipin).

---

## API keys summary

| Key | Needed for | Cost |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Real routing + geocoding | Billed. Enable **Routes API** and **Geocoding API**, restrict the key to both |
| `ANTHROPIC_API_KEY` | News extraction | Billed per article that clears the prefilter |
| `EARTHDATA_TOKEN` | VIIRS download | Free |
| `NEWSAPI_KEY` | Extra news source | Free tier, optional |

Copy `backend/.env.example` → `backend/.env` and fill in. `.env` is gitignored;
never paste a key into a PR, an issue, or a commit.
