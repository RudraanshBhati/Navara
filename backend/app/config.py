"""Central configuration. Everything tunable lives here or in the environment.

Secrets come from the environment only — never hardcode a key in this file.
Copy `backend/.env.example` to `backend/.env` and fill it in.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-relative paths, resolved from this file so they work regardless of cwd.
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
#: The one directory here that is NOT reproducible. `raw/` can be re-downloaded
#: and `processed/` can be rebuilt from it, but the news archive accumulates one
#: day at a time and a deleted day is gone — the source articles have rotated
#: off the feeds by then. Back it up; see backend/data/README.md.
ARCHIVE_DIR = DATA_DIR / "archive"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets ---
    google_maps_api_key: str = Field(default="", alias="GOOGLE_MAPS_API_KEY")
    earthdata_token: str = Field(default="", alias="EARTHDATA_TOKEN")
    # Used by the news agent for incident extraction and geolocation.
    anthropic_api_key: str = Field(default="", alias="ANTHROPIC_API_KEY")
    newsapi_key: str = Field(default="", alias="NEWSAPI_KEY")  # optional source

    # --- Server ---
    cors_origins: str = Field(default="http://localhost:5173", alias="CORS_ORIGINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # --- Routing ---
    routes_api_url: str = "https://routes.googleapis.com/directions/v2:computeRoutes"
    geocoding_api_url: str = "https://maps.googleapis.com/maps/api/geocode/json"
    max_alternatives: int = 3
    # Distance between sampled points when cutting a polyline into scoreable segments.
    segment_length_m: float = 100.0

    # --- News agent ---
    news_model: str = Field(default="claude-sonnet-5", alias="NEWS_MODEL")
    #: How far back a news run looks.
    news_lookback_hours: int = 48
    #: A news-derived incident stops influencing the score after this long.
    news_halflife_days: float = 7.0
    news_max_age_days: float = 30.0
    #: Incidents blur outward from their geocoded point by this radius, because
    #: news locations are named places ("near Saket Metro"), not GPS fixes.
    news_blur_radius_m: float = 750.0

    # --- Article body fetching ---
    #: Fetch the article itself rather than extracting from the headline. Off
    #: means the model sees a median of ~115 characters and cannot find a street
    #: name that was never in front of it. See agent/fetch.py.
    fetch_article_bodies: bool = True
    #: Seconds between requests to news sites. These are other people's servers.
    article_fetch_delay_s: float = 1.0
    article_fetch_timeout_s: float = 20.0
    #: Sent to the extractor. Enough for the lede and the paragraph naming the
    #: place; well short of a long feature's worth of unrelated place names.
    article_max_chars: int = 6000
    #: Below this a "body" is a consent wall or a subscribe stub, not an article.
    article_min_chars: int = 200

    #: GDELT asks for one request every 5 seconds and enforces it with a
    #: plain-text body that is not always accompanied by an error status.
    gdelt_min_interval_s: float = 5.0
    gdelt_max_retries: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Grid configuration
# ---------------------------------------------------------------------------
# NCT of Delhi bounding box (WGS84). Slightly padded so boundary routes don't
# fall off the edge of the grid.
DELHI_BBOX = (76.8388, 28.4041, 77.3464, 28.8834)  # (min_lon, min_lat, max_lon, max_lat)

# See docs/ARCHITECTURE.md for why 500 m: ~6k cells over Delhi, which is small
# enough to hold in memory and coarse enough that crime counts per cell are not
# hopelessly sparse.
CELL_SIZE_M = 500.0


# ---------------------------------------------------------------------------
# CSS model weights
# ---------------------------------------------------------------------------
# CSS(segment) = w_cip*CIP + w_cds*CDS + w_trc*TRC + w_ntls*NTLS + w_nsi*NSI
#
# All five layers are expressed as SAFETY contributions in [0, 1] where 1 is
# safest (see app/data/base.py for the sign convention — it is easy to get
# backwards). Weights must sum to 1.0; `validate_weights` enforces that.
#
# These are NOT equal weights. Each is argued from what the layer is actually
# made of (see docs/RESEARCH.md for citations):
#
#   CIP  0.30  Historical crime is the best-established predictor and the
#              densest data we have. Highest weight, but not a majority — it is
#              backward-looking and reflects reporting rates as much as risk.
#   NTLS 0.25  The lighting/crime link is the most strongly evidenced of all
#              five, and satellite radiance is objective and complete-coverage.
#   NSI  0.20  Our differentiator: recent, local, and the only layer that can
#              react to something that happened last night. Held below CIP
#              because news coverage is sparse and biased toward the dramatic.
#   TRC  0.15  Real signal, but derived from CIP rather than independent, so
#              weighting it highly double-counts the crime layer.
#   CDS  0.10  Weakest-confidence layer. A static POI-density proxy for footfall
#              is not validated by any paper we found. Tiebreaker only until it
#              earns more.
#
# Tune against ground truth (Safetipin's Delhi audit) rather than by intuition.
DEFAULT_WEIGHTS: dict[str, float] = {
    "cip": 0.30,  # crime incidence
    "ntls": 0.25,  # night-time lighting
    "nsi": 0.20,  # news signal (agent)
    "trc": 0.15,  # time risk coefficient
    "cds": 0.10,  # crowd density proxy
}

# Human-readable flag text, used by the explanation layer when a term is the
# dominant contributor to a segment's low score.
LAYER_FLAGS: dict[str, str] = {
    "cip": "elevated reported crime in this area",
    "cds": "few people around at this hour",
    "trc": "this time of day carries higher risk here",
    "ntls": "poorly lit stretch",
    "nsi": "recent incident reported nearby",
}

LAYER_LABELS: dict[str, str] = {
    "cip": "Crime incidence",
    "cds": "Crowd presence",
    "trc": "Time-of-day risk",
    "ntls": "Lighting",
    "nsi": "Recent news",
}


def validate_weights(weights: dict[str, float]) -> dict[str, float]:
    """Check the weight vector covers every layer and sums to 1."""
    missing = set(DEFAULT_WEIGHTS) - set(weights)
    if missing:
        raise ValueError(f"weights missing layers: {sorted(missing)}")
    unknown = set(weights) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise ValueError(f"weights has unknown layers: {sorted(unknown)}")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"weights must sum to 1.0, got {total}")
    return dict(weights)


# ---------------------------------------------------------------------------
# Risk banding — drives the green/amber/red overlay on the map.
# ---------------------------------------------------------------------------
# Thresholds on CSS (higher = safer).
BAND_SAFE = 0.66
BAND_CAUTION = 0.40


def band_for(css: float) -> str:
    if css >= BAND_SAFE:
        return "safe"
    if css >= BAND_CAUTION:
        return "caution"
    return "risk"
