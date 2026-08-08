"""FastAPI entrypoint.

uv run uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .config import get_settings
from .data import get_layers
from .grid import get_grid

log = logging.getLogger("navara")


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )

    # Load once at startup rather than lazily on the first request: a cold
    # first request that silently builds the NSI spatial index is a confusing
    # thing to debug, and we want layer problems to surface at boot.
    grid = get_grid()
    layers = get_layers()
    log.info("grid ready: %s", grid)
    for name, status in layers.status().items():
        log.info(
            "layer %-5s available=%-5s cells=%d",
            name,
            status["available"],
            status["cells_covered"],
        )
    if not settings.google_maps_api_key:
        log.warning("GOOGLE_MAPS_API_KEY not set — /routes will serve mock geometry")

    yield


app = FastAPI(
    title="NAVARA",
    version="0.1.0",
    summary="Safest-route scoring for Delhi, with a news-signal agent.",
    description=(
        "Ranks candidate walking routes on a five-layer safety model and "
        "explains the choice in plain language.\n\n"
        "Start with `GET /health` — it tells you which layers are backed by "
        "real data and whether routing is live or mocked."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {"service": "navara", "docs": "/docs", "health": "/health"}
