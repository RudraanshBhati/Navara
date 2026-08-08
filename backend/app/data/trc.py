"""TRC — Time Risk Coefficient.

How dangerous *this hour* is, inverted so 1 = a safe hour. Derived from the
timestamp distribution of the crime data itself, so it needs no external source
— but it does need incident-level records with timestamps. District-level annual
aggregates cannot produce this layer at all (see docs/DATA_SOURCES.md).

Built by ``scripts/build_trc.py``, which writes two things:
  - a city-wide 24-bin histogram of crime share by hour (always present), and
  - optional per-cell hourly profiles for cells with enough incidents to support
    one, since a market street and a residential lane do not share a risk curve.

Because TRC is derived from CIP it is *not* an independent signal — the two
correlate by construction. That is why it carries a lower weight (0.15) than
CIP: weighting it highly would double-count the same underlying data.
"""

from __future__ import annotations

import json
from datetime import datetime

from ..config import PROCESSED_DIR
from .base import NEUTRAL, Layer

HOURLY_PATH = PROCESSED_DIR / "trc_hourly.json"

#: Below this many incidents, a cell's own hourly curve is noise; fall back to
#: the city-wide one.
MIN_INCIDENTS_FOR_CELL_CURVE = 30


class TRCLayer(Layer):
    """Values are stored per (cell, hour); the global curve is the fallback."""

    name = "trc"
    confidence = "medium"

    def __init__(self) -> None:
        super().__init__()
        #: 24 floats, safety contribution per hour, city-wide.
        self._global_curve: list[float] = [NEUTRAL] * 24
        #: cell_id -> 24 floats, for cells with enough data of their own.
        self._cell_curves: dict[str, list[float]] = {}

    def load(self) -> TRCLayer:
        if HOURLY_PATH.exists():
            raw = json.loads(HOURLY_PATH.read_text(encoding="utf-8"))
            self._global_curve = [float(x) for x in raw["global"]]
            self._cell_curves = {k: [float(x) for x in v] for k, v in raw.get("cells", {}).items()}
            self._meta = raw.get("meta", {})
            self._loaded = True
        return self

    def save_curves(
        self,
        global_curve: list[float],
        cell_curves: dict[str, list[float]] | None = None,
        meta: dict | None = None,
    ) -> None:
        if len(global_curve) != 24:
            raise ValueError("global curve must have 24 hourly bins")
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "layer": self.name,
            "meta": meta or {},
            "global": global_curve,
            "cells": cell_curves or {},
        }
        HOURLY_PATH.write_text(json.dumps(payload), encoding="utf-8")
        self._global_curve = global_curve
        self._cell_curves = cell_curves or {}
        self._meta = meta or {}
        self._loaded = True

    @property
    def available(self) -> bool:
        return self._loaded

    @property
    def coverage(self) -> int:
        return len(self._cell_curves)

    def value(self, cell_id: str | None, when: datetime) -> float:
        if not self._loaded:
            return NEUTRAL
        curve = self._cell_curves.get(cell_id) if cell_id else None
        if curve is None:
            curve = self._global_curve
        return self.clamp01(curve[when.hour])
