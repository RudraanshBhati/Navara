# NAVARA frontend — not built yet

**This directory is intentionally empty.** Nothing here has been written, so
there is nothing to work around, argue with, or refactor. Whoever picks up the
frontend owns every decision in it.

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
