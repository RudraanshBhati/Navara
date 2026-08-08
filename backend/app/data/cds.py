"""CDS — Crowd Density Score.

A static POI-density proxy for footfall, modulated by a time-of-day heuristic.
Not inverted: more people around is safer ("eyes on the street").

Built by ``scripts/ingest_osm_poi.py``.

This is the weakest layer in the model and is weighted accordingly (0.10). No
paper we surveyed validates static OSM POI density as a footfall proxy, and the
time-of-day curve below is a heuristic, not a measurement. Two known failure
modes worth keeping in mind when reading its output:

  - A cell dense with shuttered shops at 2am scores as "crowded" on POI count
    alone. The time curve is what stops that, and the curve is guesswork.
  - OSM POI coverage is itself uneven across Delhi, so low density can mean
    "quiet area" or "nobody has mapped it".

Replacing this with something measured — transit ridership, mobile-derived
footfall, or Safetipin's audited "Crowd" and "Gender Diversity" parameters —
is the highest-value upgrade available to the model.
"""

from __future__ import annotations

from datetime import datetime

from .base import NEUTRAL, Layer

# Multiplier applied to a cell's static POI density, by hour of day. Peaks at
# the evening commute, bottoms out in the small hours.
HOURLY_ACTIVITY: dict[int, float] = {
    0: 0.20, 1: 0.12, 2: 0.08, 3: 0.08, 4: 0.12, 5: 0.25,
    6: 0.45, 7: 0.65, 8: 0.85, 9: 0.95, 10: 0.95, 11: 0.95,
    12: 1.00, 13: 1.00, 14: 0.95, 15: 0.95, 16: 0.95, 17: 1.00,
    18: 1.00, 19: 0.95, 20: 0.85, 21: 0.70, 22: 0.50, 23: 0.32,
}


class CDSLayer(Layer):
    name = "cds"
    confidence = "low"

    def value(self, cell_id: str | None, when: datetime) -> float:
        if cell_id is None:
            return NEUTRAL
        base = self._values.get(cell_id)
        if base is None:
            return NEUTRAL
        return self.clamp01(base * HOURLY_ACTIVITY.get(when.hour, 0.5))
