# NAVARA — MVP Build Plan (for Claude Code)

## Goal
Given an origin and destination in Delhi, return the route the system judges safest, colour-coded by risk, with a plain-language explanation of *why* — even when the safest route equals the fastest one. No SOS, live tracking, or guardian network in this phase.

## Tech stack
- **Backend**: Python (FastAPI) — hosts the CSS model, feature layers, and the route-scoring endpoint.
- **Frontend**: React + Leaflet.js (or Google Maps JS SDK) — map view, route overlay, "why" panel.
- **Routing**: Google Routes API (`computeRoutes`, `computeAlternativeRoutes: true`) — up to 3 candidate polylines per query.
- **Data store**: Start with flat files/SQLite for the pilot grid (Postgres/PostGIS later if this grows past demo scale).
- **Geo grid**: Delhi Police's 15 districts as the canonical spatial unit (GeoJSON boundaries — sourced from the community `datameet` scrape, cross-check against `data.gov.in`).

## Data pipeline (build in this order)
1. **Grid setup** — load Delhi district/police-station GeoJSON, define a fixed cell size (e.g. ~500m) within it for feature aggregation.
2. **CIP (crime layer)** — NCRB/`data.gov.in` district-wise data + Kaggle Delhi crime CSVs → normalize into a per-cell crime-density score.
3. **NTLS (lighting layer)** — VIIRS night-lights raster (via Google Earth Engine `NASA/VIIRS/002/VNP46A2`) clipped to Delhi → per-cell brightness score. OSM `lit=yes/no` tags as a secondary signal where available.
4. **TRC (time risk)** — derived from CIP's own timestamp distribution (crime-by-hour histogram) — no external source needed.
5. **CDS (crowd proxy)** — OSM POI density per cell as a static footfall proxy, modulated by a time-of-day heuristic. Flag explicitly as the weakest-confidence layer.
6. Store all four as per-cell scores, normalized 0–1, ready to be looked up by lat/lng.

## Scoring model
Keep it a **weighted linear formula**, not a black-box classifier — explainability depends on this:

```
CSS(segment) = w1*CIP + w2*CDS + w3*TRC + w4*NTLS
```

- Score each route by aggregating CSS across its segments (weighted by segment length).
- For the "why" explanation: whichever term has the largest weighted contribution to a low-scoring segment becomes that segment's flag (e.g. "poor lighting", "low pedestrian presence at this hour").
- Route-level explanation: one sentence comparing the picked route to the next-best alternative (e.g. "0.4 km longer, avoids an unlit stretch near X").
- Start with the weights already proposed in the NAVARA research paper; treat them as tunable constants, not fixed.

## Route flow (API sequence)
1. Frontend sends origin + destination to backend.
2. Backend calls Google Routes API for 2–4 alternative polylines.
3. Backend samples each polyline into segments, looks up each segment's grid cell, computes CSS.
4. Backend returns: ranked routes, per-route total CSS, per-segment flags, and the comparison sentence.
5. Frontend renders the top route on the map (green/amber/red per segment) plus a side panel with the explanation.

## Suggested repo structure
```
navara/
  backend/
    app/
      main.py              # FastAPI entrypoint
      routes_api.py        # Google Routes API wrapper
      scoring.py           # CSS model
      data/
        cip.py  cds.py  trc.py  ntls.py
      grid.py               # Delhi grid + geo lookups
    data/                   # cached datasets (crime CSVs, VIIRS extracts, geojson)
  frontend/
    src/
      App.jsx
      components/MapView.jsx
      components/RouteExplainer.jsx
  scripts/
    ingest_crime_data.py
    ingest_viirs.py
    build_grid.py
  NAVARA_MVP_BUILD_PLAN.md
```

## Build order / milestones
1. Grid + district boundaries loading and a lookup function (lat/lng → cell).
2. Ingest one real data source end-to-end (start with NCRB/Kaggle crime CSVs) into the grid — prove the pipeline before adding the other three layers.
3. Add NTLS, TRC, CDS layers.
4. Wire up Google Routes API, get raw alternative polylines rendering on a map with no scoring yet.
5. Add the CSS scoring pass over real route segments.
6. Add the explanation layer (segment flags + route comparison sentence).
7. Polish UI — color-coded overlay, why-panel, basic origin/destination search.

## Explicitly out of scope for this phase
Distress signal / SOS, live location sharing, SafeTag hardware integration, guardian network dispatch, daily news-signal agent. These come after the safe-route MVP is working end to end.

## Open decisions to make before/while building
- API keys needed: Google Maps Platform (Routes API), optionally Google Earth Engine account for VIIRS.
- Confirm final cell size / grid resolution trade-off (finer = more accurate, slower to compute).
- Decide initial weight values for the CSS formula (can start with equal weights and tune after testing).
