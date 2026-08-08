# Architecture

```
  Browser
     │  POST /routes  {origin, destination, departure_time}
     ▼
  FastAPI  (backend/app/main.py)
     │
     ├─► routes_api.py ──► Google Routes API      up to 3 alternative polylines
     │                     (or MockRoutesClient when no key is configured)
     │
     ├─► scoring.py        resample every 100 m → cell lookup → CSS per segment
     │      │
     │      └─► data/      five layers, each: (cell_id, time) → [0,1]
     │            cip  cds  trc  ntls  nsi
     │
     └─► explain.py        segment flags, why-bullets, comparison sentence
            │
            ▼
        ranked routes + explanations


  Out of band, on a schedule:

  LangGraph agent  (backend/app/agent/graph.py)
     fetch → dedupe → prefilter → extract → geocode → merge → persist
     GDELT/RSS      Claude          Google           nsi_incidents.json
```

---

## The grid

Delhi is a plain equirectangular lattice of 500 m cells over the NCT bounding
box — about 10,700 cells.

**Why not a spatial index or PostGIS?** At Delhi's latitude the distortion
across the ~50 km width of the city is under half a percent, far inside the noise
floor of the data on top of it. That makes cell lookup pure arithmetic, which
matters because we do one lookup per sampled route point — hundreds per request.

**Why 500 m?** It matches VIIRS's native resolution, so the lighting layer needs
no resampling judgement. Finer than ~250 m and crime counts per cell get too
sparse to mean anything.

**Cell ids** (`r0046c0072`) are stable only as long as the bbox and cell size
don't change. Both are pinned in `data/processed/grid.json`, and
`scripts/build_grid.py` refuses to overwrite a manifest that disagrees with the
config — changing the grid silently would make every ingested layer point at the
wrong places on the map.

## The layers

All five expose `value(cell_id, when) → [0, 1]`, **1 = safest**. Three are
inverted relative to what they are built from; the sign convention is documented
at the top of [`data/base.py`](../backend/app/data/base.py) and is the single
easiest thing to get catastrophically backwards.

Layers load from `data/processed/` at startup. **A missing layer is not an
error** — it returns `NEUTRAL` (0.5) and reports `available: false` on
`/health`. A fresh clone runs, serves routes, and tells you loudly that it is
scoring on placeholders.

NSI is the exception in two ways: it stores *incident records* rather than
per-cell values (because the signal decays continuously and should not need a
rebuild to stay current), and its no-data default is **1.0, not 0.5** — it only
ever penalises, so defaulting it to neutral would drag every score in the city
down by a constant containing no information.

## Scoring

```
CSS(segment) = Σ  wᵢ · valueᵢ(cell, time)
```

Two derived quantities do the work:

- **contribution** `wᵢ · vᵢ` — what this layer added
- **deficit** `wᵢ · (1 - vᵢ)` — what this layer took away

Explanations are driven by the **deficit**, not the contribution. On a good
segment the largest contribution is just whichever layer carries the biggest
weight; the deficit is what is actually dragging the score down.

A layer may only be blamed for a cell it has data for. Otherwise an unbuilt
layer sits at NEUTRAL, leaving a deficit of half its weight — enough to stamp
every road in Delhi with a confident accusation sourced from nothing.

Routes aggregate as a **length-weighted mean**, so a long unlit stretch counts
for more than a brief one. We also compute the **worst contiguous stretch**
rather than the single worst segment: one bad 100 m sample is usually a data
artefact, 600 m of continuous amber is a road.

## The news agent

A LangGraph state machine rather than a script, for three reasons: each node is
independently testable with a hand-built state dict; the state carries `stats`
and `errors` through so a run that produced nothing tells you *which* stage
failed (the difference between "quiet news day" and "the RSS feeds moved" is
otherwise invisible); and adding a human-review gate before `persist` becomes an
edge change rather than a rewrite.

That gate is likely to be needed. This layer moves a map people navigate by, on
the strength of a language model reading a headline.

Design choices and their reasons are in
[`RESEARCH.md § What we added`](RESEARCH.md#what-we-added-the-news-agent).

## Degradation, by design

| Missing | Behaviour |
|---|---|
| `GOOGLE_MAPS_API_KEY` | Mock router; `provider: "mock"` + warning in every response |
| Any data layer | Scores `NEUTRAL`; `/health` and `/routes` both warn; layer cannot flag segments |
| `ANTHROPIC_API_KEY` | Agent fetches and dedupes, fails at extract, records the error, still ages out expired incidents |
| A dead news source | Logged, run continues on the others |

The rule: **degrade loudly, never silently.** Every fallback announces itself in
the response body. A system that quietly returns plausible numbers from no data
is more dangerous than one that fails.

## Deliberate non-choices

- **No database.** ~10,700 cells of JSON. Postgres/PostGIS when there is a
  reason, not before.
- **No async in the scoring path.** It is CPU-bound arithmetic over in-memory
  dicts.
- **No ML model.** See [`RESEARCH.md § Why a linear model`](RESEARCH.md#why-a-linear-model).
- **No user accounts, no live tracking, no SOS.** Out of scope for this phase —
  see the build plan.
