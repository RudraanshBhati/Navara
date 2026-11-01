# NAVARA frontend

A Google-Maps-style interface for Delhi NCR: search with typo correction and
suggestions, police / hospitals / metro / bus stops / shops on the map,
place cards, and walking directions where every alternative is coloured by
safety and explained by the backend. React + Vite + Leaflet, no keys needed.

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173, expects the backend on :8000
```

Places on the map come from OpenStreetMap; build them once on the backend:

```bash
cd backend
uv run python ../scripts/fetch_map_pois.py    # ~2 min, writes data/processed/map_pois.json
uv run python ../scripts/fetch_roads.py       # ~1 min, main roads for the road-type term
uv run python ../scripts/fetch_cameras.py     # mapped CCTV near routes
uv run python ../scripts/fetch_signals.py     # mapped traffic signals, for riders and drivers
```

How the pieces fit:

| Feature | Where it comes from |
|---|---|
| Base map | [OpenFreeMap](https://openfreemap.org) "Liberty" vector style via MapLibre, inside Leaflet. Free, no key |
| Search, typo correction | `GET /places/autocomplete` — Google Places when the backend has a key, fuzzy offline search over landmarks and OSM places when not |
| Suggestions before typing | Your location (if shared) and recent places, kept in this browser's `localStorage` |
| Places on the map | `GET /pois` for the visible area. More appear as you zoom in; overlapping markers are thinned, police and hospitals kept first |
| Directions | `POST /routes` with `travel_mode` — Walking, Two-wheeler or Car. A Safest / Fastest switch picks between the two, with the backend's `tradeoff` sentence under it; or click any route card, grey line or duration bubble. Departure chips (Now, 9 PM, 11 PM, 1 AM) make night trips one tap |
| Route facts | The selected card lays out the backend's `facts` as stats: places likely open, share on main roads, longest stretch with nothing open. That stretch is drawn as a dark dotted line, and the open places are pinned along the route |
| Isolated stretches, help, CCTV, signals | The route's `longest_isolated` (purple dashes), `farthest_help` (red line to the nearest place likely open), `cameras` (grey dots) and, on a vehicle, `signals` (amber markers). Reported beside the score, not part of it |
| Time to leave | `departure_options` for the selected route: tap a time to re-run the trip then. `departure_note` says what it would change |
| Shareable view | The URL holds the view, Google-style: `#@28.63150,77.21670,16z` |

MapLibre's web worker is bundled explicitly (`?worker&url` in
`MapView.jsx`); without that the production build ships without it and the
map renders blank.

The backend is finished and running. Start at
**[`docs/FRONTEND_GUIDE.md`](../docs/FRONTEND_GUIDE.md)** — it documents the
exact API contract, the response shape, and what the UI needs to communicate.

## The one thing that makes this easy

**You do not need any API keys to build the entire UI.**

The backend runs with zero configuration. With no Google Maps key it serves
real, correctly-shaped route responses from an offline mock router; with no data
layers built it scores them on neutral values. Everything is populated, nothing
crashes, and every response tells you it is doing this via `provider: "mock"`
and a `warnings` array.

```bash
cd backend
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload
```

Then <http://localhost:8000/docs> for the live schema, and
`GET /health` to see what is real and what is placeholder.

## What to build

Three things, in this order:

1. **Map view** — Delhi map, origin/destination pins, the recommended route
   drawn segment-by-segment in green/amber/red, alternatives greyed behind it.
2. **Why-panel** — the actual product. The map shows *what*; this says *why*.
   The backend hands you finished sentences (`summary`, `reasons`,
   `comparison`) and a per-layer score breakdown. Render them; don't write your
   own reassuring copy on top.
3. **Origin/destination input** — search or click-to-place. Least interesting,
   do it last.

## Suggested stack — suggested, not decided

React + Vite + Leaflet with OpenStreetMap tiles. Leaflet needs no key, which
keeps the no-keys-required property above intact. If you would rather use
something else, that is genuinely your call — the backend is a plain JSON API
and does not care.

## Two things that are not negotiable

Everything else is yours, but these two are correctness, not taste:

- **Never hide `warnings` or `provider`.** A demo that looks authoritative while
  running on synthetic geometry is worse than one that shows an ugly banner.
- **Never invent explanation text.** Every sentence the user reads about safety
  must come from the backend, where it is generated from actual terms in the
  score. This is a tool people may use to decide which road to walk down at
  night; a plausible-sounding sentence with nothing behind it is the one failure
  mode we cannot accept.
