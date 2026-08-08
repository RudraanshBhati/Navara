# NAVARA backend

FastAPI service that scores candidate routes through Delhi on a five-layer
safety model, and the LangGraph agent that keeps the news layer fresh.

## Quick start

```bash
cd backend
uv venv --python 3.12
uv pip install -e ".[dev]"
cp .env.example .env          # then fill in your keys
uv run uvicorn app.main:app --reload
```

Open <http://localhost:8000/docs> for the interactive API.

`GET /health` reports which of the five layers are backed by real data. On a
fresh clone that is none of them — the service still runs and returns routes,
scored on neutral placeholders, so you can develop the frontend before the data
pipeline is finished. Build the layers with the scripts in [`../scripts/`](../scripts/).

## Layout

| Path | What it is |
|---|---|
| [`app/main.py`](app/main.py) | FastAPI entrypoint, CORS, layer loading |
| [`app/api/routes.py`](app/api/routes.py) | HTTP endpoints |
| [`app/grid.py`](app/grid.py) | Delhi lattice, lat/lng → cell lookup, distance helpers |
| [`app/scoring.py`](app/scoring.py) | The CSS model |
| [`app/explain.py`](app/explain.py) | Turns scores into sentences |
| [`app/routes_api.py`](app/routes_api.py) | Google Routes API wrapper + polyline decoding |
| [`app/data/`](app/data/) | The five feature layers |
| [`app/agent/`](app/agent/) | LangGraph news agent |
| [`data/raw/`](data/raw/) | Downloaded sources (gitignored) |
| [`data/processed/`](data/processed/) | Built per-cell layers (gitignored) |

## Testing

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

Tests do not hit the network or require API keys.
