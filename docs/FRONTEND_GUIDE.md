# Frontend guide — what NAVARA is, and what the API gives you

You are picking up the frontend. This document is everything you need: what the
product is trying to do, the exact API contract, and the two rules that are not
style preferences.

---

## What we are building

Delhi. Someone needs to walk somewhere. Google Maps will give them the fastest
route, which at 11pm may run down an unlit service lane with nobody on it.

NAVARA scores every route Google offers on how safe it is, ranks them, and —
this is the part that matters — **explains the choice in plain language.** Not
"safety score 0.72", but *"400 m longer than the fastest route, avoiding a
600 m unlit stretch near Lajpat Nagar."*

The explanation is the product. A safety score nobody trusts is a number; a
score that shows its reasoning is a decision someone can actually make.

## How the score works

Delhi is cut into a grid of 500 m cells. Every cell carries five scores, each
in `[0, 1]` where **1 is safest**:

| Key | Layer | What it is | Weight |
|---|---|---|---|
| `cip` | Crime incidence | Historical reported crime, severity-weighted | 0.30 |
| `ntls` | Lighting | Satellite night-lights radiance (VIIRS) | 0.25 |
| `nsi` | Recent news | **Our differentiator.** An agent reads Delhi news daily, extracts located incidents, and decays them over a week | 0.20 |
| `trc` | Time-of-day risk | Crime-by-hour curve for this cell | 0.15 |
| `cds` | Crowd presence | POI density as a footfall proxy. Weakest layer — treat as a hint | 0.10 |

A route is sampled every 100 m; each sample gets `CSS = Σ weightᵢ × layerᵢ`;
the route score is the length-weighted mean. Because it is a plain weighted sum,
every explanation is exact arithmetic rather than interpretation — that is why
the model is deliberately not a neural net.

**Time of day changes everything.** The same route scores differently at 2pm and
2am. Always send `departure_time`, and make it visible in the UI that the rating
is time-specific.

---

## Running the backend

**You need no API keys.** With no Google key it serves correctly-shaped routes
from an offline mock router; with no data layers built it scores them neutrally.
Nothing crashes, everything is populated.

```bash
cd backend
uv venv --python 3.12
uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload
```

- Live schema: <http://localhost:8000/docs>
- What is real vs. placeholder: <http://localhost:8000/health>

Point your dev server's `/api` proxy at `http://localhost:8000`.

---

## The API

Three endpoints. `POST /routes` is the only one you strictly need.

### `POST /routes`

```jsonc
{
  "origin":      { "lat": 28.6139, "lng": 77.2090 },
  "destination": { "lat": 28.5355, "lng": 77.2410 },
  "departure_time": "2026-03-15T23:30:00Z"   // optional, defaults to now
}
```

Response (trimmed to one route and two segments):

```jsonc
{
  "routes": [
    {
      "rank": 0,                    // 0 is the recommendation
      "recommended": true,          // exactly one route has this
      "label": "Direct",
      "css": 0.6,                   // 0-1, higher is safer
      "band": "caution",            // "safe" | "caution" | "risk"
      "distance_m": 9260.8,
      "duration_s": 6859.9,
      "polyline": "{ssmDg{fvMdGiBbGkB...",   // Google-encoded, precision 5

      "segments": [
        {
          "start": { "lat": 28.6139, "lng": 77.2090 },
          "end":   { "lat": 28.6130, "lng": 77.2093 },
          "css": 0.6,
          "band": "caution",
          "flag": "poorly lit stretch",   // or null when the segment is fine
          "cell_id": "r0046c0072"
        }
        // ... ~100 m apart, and CONTIGUOUS: each segment's `end` is exactly
        //     the next one's `start`, so you can draw them without gaps
      ],

      "layer_contributions": {      // what each layer actually contributed
        "cip": 0.15, "ntls": 0.125, "nsi": 0.2, "trc": 0.075, "cds": 0.05
      },

      "worst_stretch": {            // the longest bad run, or null
        "length_m": 600, "css": 0.31, "band": "risk",
        "reason": "ntls",           // layer key to blame
        "start": { "lat": 28.60, "lng": 77.21 },
        "end":   { "lat": 28.59, "lng": 77.22 }
      },

      "coverage": 0.83,             // fraction of route with real data behind it

      "summary": "9.3 km, about 1h 54m on foot. This route is mostly fine with some stretches to watch.",
      "reasons": [                  // why-panel bullets, strongest first
        "600 m of this route scores below its own average, mostly on lighting.",
        "Scored for a night-time walk — lighting and how busy the streets are both count here."
      ],

      "news": [                     // recent incidents near this route
        {
          "headline": "Chain snatching reported near Saket Metro",
          "url": "https://...",
          "source": "Hindustan Times Delhi",
          "occurred_at": "2026-03-14T00:00:00+00:00",
          "category": "snatching",
          "location": "Saket Metro Station, New Delhi",
          "confidence": 0.72        // show this; do not hide it
        }
      ]
    }
  ],

  "comparison": "500 m longer than the fastest route, avoiding 600 m of lighting on the alternative. Safety score 0.80 against 0.55.",
  "departure_time": "2026-03-15T23:30:00Z",
  "provider": "mock",               // "google" when a real key is configured
  "weights": { "cip": 0.3, "ntls": 0.25, "nsi": 0.2, "trc": 0.15, "cds": 0.1 },
  "warnings": [
    "Mock routing provider — geometry is synthetic.",
    "Layers with no data (scored neutral): cds, cip, nsi, ntls, trc."
  ]
}
```

Routes come back **already sorted safest-first**. Don't re-sort them.

Errors: `422` for a malformed request or a point outside Delhi, `502` if the
routing provider fails. Both carry a human-readable `detail` string — show it.

### `GET /health`

Which layers are real, the grid dimensions, and whether routing is live. Call it
on load; if `warnings` is non-empty, put a banner up.

### `GET /cell?lat=&lng=&at=`

Every layer's value for one point, with the weighted contribution broken out.
This is the "why is this road red?" endpoint — good for a debug overlay or a
click-on-the-map inspector.

---

## Suggested colours

The API sends a `band` name, never a colour — the palette is yours. Something
in this neighbourhood works:

| Band | Meaning | Suggestion |
|---|---|---|
| `safe` | CSS ≥ 0.66 | green `#2e9e5b` |
| `caution` | 0.40 ≤ CSS < 0.66 | amber `#e0a52b` |
| `risk` | CSS < 0.40 | red `#d4443c` |

Draw the recommended route segment-by-segment (one line per segment, coloured by
its band), alternatives as thin grey dashed lines behind it.

---

## What to build, in order

1. **Map** — Delhi, origin/destination pins, recommended route coloured by
   segment, alternatives greyed behind.
2. **Why-panel** — the actual product. `summary`, `reasons`, `comparison`, the
   `layer_contributions` breakdown, and the `news` list with working links.
3. **Origin/destination input** — search or click-to-place. Do this last.

Stack is your call. React + Vite + Leaflet with OpenStreetMap tiles is the
obvious choice mainly because Leaflet needs no API key, which preserves the
"clone and run" property. The backend is plain JSON and does not care.

---

## Two rules that are not style preferences

**1. Never hide `warnings` or `provider`.**

A demo that looks authoritative while running on synthetic geometry and neutral
placeholder data is worse than one showing an ugly banner. When `provider` is
`"mock"`, say so on screen.

**2. Never write your own explanation text.**

Every sentence a user reads about safety must come from the backend, where it is
generated from actual terms in the score. Do not add encouraging copy, do not
paraphrase a score into a claim, do not round `coverage: 0.2` up to "verified".

People may use this to decide which road to walk down at night. A plausible
sentence with nothing behind it is the one failure this project cannot afford —
and it is much easier to write by accident than it sounds.

---

## Where to ask

- Response shape questions → <http://localhost:8000/docs> is generated from the
  real code and is never out of date.
- Why a score is what it is → `GET /cell`, then
  [`backend/app/scoring.py`](../backend/app/scoring.py).
- Why the model is built this way → [`ARCHITECTURE.md`](ARCHITECTURE.md) and
  [`RESEARCH.md`](RESEARCH.md).
- If a field you need isn't there, open an issue. Adding to the response is
  cheap; working around a missing field is not.
