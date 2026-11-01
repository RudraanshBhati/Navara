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

Product: **VNP46A3** ("Black Marble" monthly composite, near-nadir snow-free
band), 500 m native resolution — which matches our cell size exactly, so no
resampling judgement is needed. The nightly **VNP46A2** still works.

```bash
uv run python scripts/download_viirs.py          # last 12 months, ~1.6 GB
uv run python scripts/ingest_viirs.py "backend/data/raw/VNP46A3*.h5"
```

Monthly composites carry a quality flag, and the ingest keeps only pixels built
from more than three clear nights. Over Delhi that flag is decisive in the
monsoon: in the October 2026 build, **July, August and September 2026 had no
good-quality pixel at all**, and June lost 78% of cells. A year of months
leaves about 8 clear ones per cell, which is what the layer is the median of.

(For the record: the earlier layer, built from 12 monsoon nights of VNP46A2,
turned out to correlate at r = 0.976 with this one — NASA's gap-filling had
substituted historical values. Close, but this one is measured.)

Two routes:

| Source | Access | Notes |
|---|---|---|
| [NASA Black Marble](https://blackmarble.gsfc.nasa.gov/) | Free [Earthdata login](https://urs.earthdata.nasa.gov/), instant | **Recommended.** Grab tile `h25v06`, clip to Delhi. No GEE dependency |
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

## Crowd proxy (CDS) — population, POIs, transit

Three inputs, combined per cell and per hour by `scripts/build_cds.py`. Each is
optional; the build uses whatever is present and says which.

| Input | Source | Access | Coverage |
|---|---|---|---|
| Residents | [Meta/CIESIN High Resolution Settlement Layer](https://registry.opendata.aws/dataforgood-fb-hrsl/), 30 m | Free, no key, CC BY 4.0. Read as a window straight from AWS | Complete: 9,107 of 10,700 cells populated; the rest are fields, ridge, river |
| POIs | OpenStreetMap via [Overpass](https://overpass-api.de/) | Free, no key; cached | Patchy: 2,505 cells have any mapped amenity |
| Transit | [Delhi Open Transit Data](https://otd.delhi.gov.in/data/static/) GTFS, buses and Metro | Free, but a form (name, email, purpose) and terms acceptance per download — a manual step. Save zips to `backend/data/raw/gtfs/` | Every stop; the only input measured by hour. Bus feed last updated June 2024 |

```bash
uv run python scripts/ingest_population.py   # ~5 s, ~1 MB cached
uv run python scripts/ingest_osm_poi.py      # once; cached
uv run python scripts/build_cds.py
```

Read the caveats in [`app/data/cds.py`](../backend/app/data/cds.py) before
trusting the output. Residents are not footfall, the time-of-day curves are
judgement rather than measurement, and the layer is still confidence "low".

---

## Map places — display only

Police stations, hospitals, metro and railway stations, bus stops, pharmacies,
toilets, petrol pumps, ATMs, restaurants and named shops across Delhi NCR, from
OpenStreetMap via Overpass (three queries; one NCR-wide query timed out at the
gateway). About 21,600 places, 3.4 MB.

```bash
uv run python scripts/fetch_map_pois.py
```

Kept separate from the crowd layer's POI cache on purpose, so that changing
what the map shows can never move a safety score. Coverage gaps worth knowing:
OSM has ~2,400 bus stops for NCR, while Delhi's own GTFS lists 3,464 for Delhi
alone.

## Street-level terms — road type and open places

Two scoring terms are computed per route segment rather than per cell
(`app/features.py`), from OpenStreetMap:

```bash
uv run python scripts/fetch_roads.py       # ~30,000 main-road ways for NCR, 8.4 MB
uv run python scripts/fetch_map_pois.py    # the map places, which also feed "open places"
```

- **Road type** — is the segment within 30 m of a motorway, trunk, primary,
  secondary or tertiary road. Only main roads are fetched; OSM's coverage of
  those in Delhi is near complete, so "not near one" reliably means a lane.
- **Open places** — places within 75 m likely open at that hour. Opening hours
  come from OSM where tagged (**8%** of NCR places, 474 of them 24/7) and from
  per-category defaults otherwise, listed in `DEFAULT_HOURS`. Where OSM has
  mapped nothing within 200 m, the term is unmeasured rather than zero — a
  blank map is not an empty street.

The idea of scoring road type, open places and the longest dark gap comes from
Aevy TV's "Mapping UNSAFE streets for women" (Oct 2026).

## Route facts beside the score — cameras, signals, isolation, help

Reported on each route and kept out of the score (`app/features.py`):

```bash
uv run python scripts/fetch_cameras.py     # man_made=surveillance, 3,261 outdoor
uv run python scripts/fetch_signals.py     # highway=traffic_signals, 1,513 nodes
```

- **Cameras** — mapped outdoor CCTV within 50 m. Measured November 2026: 45% of
  them sit in 20 of 10,700 cells, and only 189 of 3,496 densely populated cells
  have any. That measures where volunteers mapped, so a camera can be cited but
  absence says nothing. Delhi Police and PWD hold the real locations (RTI).
  Check the count after fetching: on 1 November 2026 a fallback Overpass mirror
  answered with 2,235 cameras where the primary server had 3,283.
- **Traffic signals** — mapped signal nodes within 30 m of a two-wheeler or car
  route, merged within 120 m so one junction counts once. Live timers are not
  available: Mappls' are reported only for Bengaluru, with no public feed found.
  A road under a flyover can match the signal above it, so the count is approximate.
- **Isolated stretches** — fewer than 25 detected residents in the cell (HRSL)
  and no mapped place within 200 m, for at least 300 m. Unknown outside the grid.
- **Farthest from help** — straight line to the nearest police station, hospital
  or petrol pump likely open. Clinics are excluded. Mapped places only, so the
  real distance may be shorter; following roads, it may be longer.

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

## Data search, October 2026 — what exists for Delhi NCR

A search for anything usable. The short version: **there is still no public
incident-level crime dataset with coordinates and timestamps for Delhi NCR.**

**Worth requesting** (gated; lead times are weeks):

| Source | What | Access |
|---|---|---|
| Safetipin Delhi NCR audits | ~44,000 street-level audits, 9 parameters 0–3 incl. lighting and crowd, with time and location; shapefile | Data use agreement by email — see the [World Bank record](https://reproducibility.worldbank.org/metadata/export/565/json). The best validation set, and the only street-level lighting source |
| Safecity (Red Dot Foundation) | Crowdsourced harassment reports with type, time and location; 45,000+ stories | Partnership / request (info@safecity.in). Located *and* timestamped — but reporting-biased |
| [Praja Foundation reports](https://praja.org/) | Registered crime by police station (156 stations), from RTI | PDF tables. Needs station boundaries: the GSDL-sourced KML once on OpenCity now returns 404, and GSDL prohibits reproduction — obtain via RTI or GSDL licence |

**Checked and not usable:** Kaggle "Delhi crime" datasets (14.7 KB and 2 KB
aggregates; the only crime set with coordinates found was Bangalore, paywalled
on IEEE DataPort); OSM `lit` tags (0.4% of street segments); streetlight and
CCTV locations (unpublished — RTI only); DMRC per-station ridership
(unpublished); Gurugram/Noida FIRs (no bulk data); Delhi Police online FIRs
(sexual offences excluded by the 2016 Supreme Court order, per-record lookups
with personal names — not a dataset). SDGSAT-1 night lights (10–40 m) would
solve the resolution problem, but access runs through a CAS programme and India
coverage is unconfirmed.

**Similar projects on GitHub** — a dozen India safe-route apps, all
hackathon-scale. None had better data: they use NCRB district totals (e.g.
`Padmin-12/SafeRoutePlanner-Delhi`, via India Data Portal's CKAN API),
OSM road class and `lit` tags (`Dhairya-Shukla/SafeRoute`), synthetic FIRs
(`yashendra767/Trinetra-AI`), or ward risk apportioned by distance from the
city centre (`Eeman1113/crimeradar`). The one idea worth borrowing is OSM road
class as a night-time signal — unlike `lit`, `highway=*` is complete.

## API keys summary

| Key | Needed for | Cost |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | Real routing + geocoding | Billed. Enable **Routes API** and **Geocoding API**, restrict the key to both |
| `ANTHROPIC_API_KEY` | News extraction | Billed per article that clears the prefilter |
| `EARTHDATA_TOKEN` | VIIRS download | Free |
| `NEWSAPI_KEY` | Extra news source | Free tier, optional |

Copy `backend/.env.example` → `backend/.env` and fill in. `.env` is gitignored;
never paste a key into a PR, an issue, or a commit.
