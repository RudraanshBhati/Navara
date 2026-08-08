# NAVARA

**The safest route through Delhi, and a plain-language explanation of why.**

Navigation apps optimise for time. At 11pm that can route you down an unlit
service lane with nobody on it. NAVARA scores every route Google offers on five
safety layers, ranks them, and explains the choice:

> *500 m longer than the fastest route, avoiding a 600 m unlit stretch near
> Lajpat Nagar. Safety score 0.80 against 0.55.*

The explanation is the product. A safety score nobody can interrogate is just a
number; a score that shows its reasoning is a decision someone can act on.

---

## Status

| Part | State |
|---|---|
| Scoring engine, API, news agent | **Working.** 87 tests passing, lint clean |
| Data layers | **Not ingested.** Scripts written; datasets not yet loaded |
| Frontend | **Not started.** See [`docs/FRONTEND_GUIDE.md`](docs/FRONTEND_GUIDE.md) |

The backend runs today with no API keys and no data — it serves correctly-shaped
routes from an offline mock router and says so loudly in every response.

## Quick start

```bash
cd backend
uv venv --python 3.12
uv pip install -e ".[dev]"
cp .env.example .env          # optional — it runs without keys
uv run uvicorn app.main:app --reload
```

- Interactive API: <http://localhost:8000/docs>
- What's real vs. placeholder: <http://localhost:8000/health>

```bash
uv run pytest            # 87 tests, no network, no keys
uv run ruff check .
```

## How the score works

Delhi is cut into 500 m cells. Each carries five scores in `[0, 1]`, 1 = safest:

| Layer | What it is | Weight |
|---|---|---|
| **CIP** | Historical crime, severity-weighted | 0.30 |
| **NTLS** | Satellite night-lights (VIIRS) | 0.25 |
| **NSI** | **Recent news, via our agent** | 0.20 |
| **TRC** | Crime-by-hour curve | 0.15 |
| **CDS** | POI density as footfall proxy *(weakest — treat as a hint)* | 0.10 |

```
CSS(segment) = Σ weightᵢ × layerᵢ(cell, time)
```

A route is sampled every 100 m and scored as a length-weighted mean. The model
is a plain weighted sum on purpose: every explanation is exact arithmetic rather
than interpretation. [Why not a neural net.](docs/RESEARCH.md#why-a-linear-model)

**Time of day changes the answer.** The same route scores differently at 2pm and
2am.

## The news agent

Every other layer is historical — it can tell you a road has been dangerous for
years, but not that something happened there last night.

NSI is a LangGraph agent that reads Delhi news, extracts located incidents,
geocodes them, and decays their influence with a one-week halflife:

```
fetch → dedupe → prefilter → extract → geocode → merge → persist
GDELT/RSS        Claude       Google Geocoding    nsi_incidents.json
```

It is the part of NAVARA that the [prior work we surveyed](docs/RESEARCH.md)
does not have — and the part carrying the most risk, which is why every incident
gets a confidence score, duplicate coverage is collapsed before extraction, and
news flags cite their source rather than asserting a fact.

```bash
uv run python scripts/run_news_agent.py --offline --dry-run   # no keys needed
```

## Documentation

| | |
|---|---|
| [**FRONTEND_GUIDE.md**](docs/FRONTEND_GUIDE.md) | **Start here if you're building the UI.** Full API contract |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | How the pieces fit and why |
| [RESEARCH.md](docs/RESEARCH.md) | Papers surveyed, what we took, what we added |
| [DATA_SOURCES.md](docs/DATA_SOURCES.md) | Where each dataset comes from |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Setup, conventions, PR flow |
| [NAVARA_MVP_BUILD_PLAN.md](NAVARA_MVP_BUILD_PLAN.md) | The original plan |

## Layout

```
backend/
  app/
    grid.py       Delhi lattice, lat/lng → cell
    scoring.py    the CSS model
    explain.py    scores → sentences
    routes_api.py Google Routes + offline mock
    data/         the five layers
    agent/        LangGraph news agent
  tests/          87 tests, no network
scripts/          grid build + data ingest + agent runner
frontend/         empty, deliberately
docs/
```

## Degrade loudly, never silently

Every fallback announces itself in the response body — mock routing, missing
layers, thin data coverage, low-confidence news extractions. A layer with no
data is not allowed to explain a segment.

A system that quietly returns plausible numbers from no data is more dangerous
than one that fails. People may use this to decide which road to walk down at
night.

## Out of scope for this phase

SOS/distress signal, live location sharing, SafeTag hardware, guardian network
dispatch. These come after safe routing works end to end.

## Licence

MIT — see [LICENSE](LICENSE).
