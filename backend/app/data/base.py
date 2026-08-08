"""Shared contract for the five feature layers.

SIGN CONVENTION — read this before touching any layer
-----------------------------------------------------
Every layer returns a **safety contribution in [0, 1] where 1 is safest.**

That means three of the five layers are inverted relative to the quantity they
are built from:

    CIP   crime density        -> 1 - normalised(severity-weighted density)
    TRC   risk at this hour     -> 1 - normalised(hourly crime share)
    NSI   recent incidents     -> 1 - normalised(decayed incident pressure)
    NTLS  brightness           -> normalised(radiance)          (not inverted)
    CDS   footfall proxy       -> normalised(POI density)       (not inverted)

Getting this backwards produces a model that confidently routes people through
the worst streets in the city, and it will not look wrong in any test that only
checks the output is in [0, 1]. The inversion happens once, at ingest time, in
each layer's build step — never at scoring time.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path

from ..config import PROCESSED_DIR

# Value used for a cell with no data. 0.5 is deliberately neutral: it neither
# rewards nor punishes an unmeasured area. Coverage is reported separately so a
# route through unmeasured ground can be flagged as low-confidence rather than
# quietly scored as average.
NEUTRAL = 0.5


class Layer(ABC):
    """A per-cell feature layer."""

    name: str
    #: Rough confidence in this layer's signal, used to caveat explanations.
    #: One of "high" | "medium" | "low".
    confidence: str = "medium"

    def __init__(self) -> None:
        self._values: dict[str, float] = {}
        self._meta: dict = {}
        self._loaded = False

    # -- persistence ------------------------------------------------------

    @property
    def path(self) -> Path:
        return PROCESSED_DIR / f"{self.name}.json"

    def load(self) -> Layer:
        """Load per-cell values from disk. A missing file is not an error —
        the layer reports itself unavailable and returns NEUTRAL everywhere."""
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._values = {k: float(v) for k, v in raw["cells"].items()}
            self._meta = raw.get("meta", {})
            self._loaded = True
        return self

    def save(self, values: dict[str, float], meta: dict | None = None) -> None:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"layer": self.name, "meta": meta or {}, "cells": values}
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self._values = values
        self._meta = meta or {}
        self._loaded = True

    # -- query ------------------------------------------------------------

    @property
    def available(self) -> bool:
        """True once real data has been ingested for this layer."""
        return self._loaded and bool(self._values)

    @property
    def coverage(self) -> int:
        return len(self._values)

    @property
    def meta(self) -> dict:
        return self._meta

    def has(self, cell_id: str | None) -> bool:
        return cell_id is not None and cell_id in self._values

    @abstractmethod
    def value(self, cell_id: str | None, when: datetime) -> float:
        """Safety contribution in [0, 1] for this cell at this time."""

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def clamp01(x: float) -> float:
        return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


class StaticLayer(Layer):
    """A layer whose value does not depend on time of day."""

    def value(self, cell_id: str | None, when: datetime) -> float:  # noqa: ARG002
        if cell_id is None:
            return NEUTRAL
        return self._values.get(cell_id, NEUTRAL)


# ---------------------------------------------------------------------------
# Normalisation used by the ingest scripts.
# ---------------------------------------------------------------------------


def normalise(
    raw: dict[str, float],
    *,
    invert: bool = False,
    percentile_clip: float = 0.95,
) -> dict[str, float]:
    """Scale raw per-cell quantities into [0, 1].

    Clips at a high percentile before scaling. Delhi's crime counts and night
    radiance are both heavy-tailed — Connaught Place is an order of magnitude
    brighter than a residential lane — and scaling on the raw max would squash
    every ordinary cell into the bottom few percent of the range, leaving the
    layer with almost no discriminating power where it matters.

    Set ``invert=True`` for quantities where *more* means *less safe*.
    """
    if not raw:
        return {}

    values = sorted(raw.values())
    idx = min(int(len(values) * percentile_clip), len(values) - 1)
    hi = values[idx]
    lo = values[0]
    span = hi - lo

    if span <= 0:
        # The clip landed on a flat part of the distribution — e.g. most cells
        # hold an identical value and only a few outliers differ. Fall back to
        # the true maximum so those outliers are still ranked; if even that is
        # flat, the data carries no spatial information and every cell gets a
        # neutral 0.5 rather than a confident 0.0.
        hi = values[-1]
        span = hi - lo
        if span <= 0:
            return dict.fromkeys(raw, 0.5)

    out: dict[str, float] = {}
    for cell_id, v in raw.items():
        scaled = 0.0 if span <= 0 else (v - lo) / span
        scaled = Layer.clamp01(scaled)
        out[cell_id] = 1.0 - scaled if invert else scaled
    return out


# ---------------------------------------------------------------------------
# Offence severity weights.
# ---------------------------------------------------------------------------
# Counting a pickpocketing equal to an assault understates exactly the risk this
# product exists to surface. Several papers in docs/RESEARCH.md apply a
# severity-weighted crime score instead of a raw count and report a real gain.
#
# These are ordinal judgements calibrated to *pedestrian* risk, not to sentencing
# guidelines — vehicle theft is a serious crime but says little about whether a
# person walking past is in danger. Revisit them with the same scrutiny as the
# CSS weights; they are just as load-bearing and much easier to overlook.
SEVERITY_WEIGHTS: dict[str, float] = {
    "sexual_assault": 1.0,
    "assault": 0.9,
    "kidnapping": 0.9,
    "murder": 0.85,
    "harassment": 0.8,
    "stalking": 0.75,
    "robbery": 0.7,
    "snatching": 0.6,
    "theft": 0.35,
    "burglary": 0.3,
    "vehicle_theft": 0.2,
    "other": 0.4,
}

DEFAULT_SEVERITY = SEVERITY_WEIGHTS["other"]


def severity_of(offence: str | None) -> float:
    """Map a free-text offence label onto a severity weight.

    Deliberately forgiving about input: source datasets label the same offence a
    dozen different ways, and an unmatched label falling back to the "other"
    weight is far better than dropping the incident.
    """
    if not offence:
        return DEFAULT_SEVERITY
    key = offence.strip().lower().replace(" ", "_").replace("-", "_")
    if key in SEVERITY_WEIGHTS:
        return SEVERITY_WEIGHTS[key]
    for known, weight in SEVERITY_WEIGHTS.items():
        if known in key or key in known:
            return weight
    return DEFAULT_SEVERITY
