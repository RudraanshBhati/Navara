"""CDS — Crowd Density Score.

An estimate of how many people are around a cell at a given hour. Not
inverted: more people around is safer ("eyes on the street").

Built by ``scripts/build_cds.py`` from up to three inputs, each optional:

  population   Meta/CIESIN High Resolution Settlement Layer, people per 30 m
               pixel summed per cell (scripts/ingest_population.py). Complete
               coverage, derived from detected buildings — so zero here is a
               measurement, not a gap.
  POIs         OpenStreetMap amenities and shops (scripts/ingest_osm_poi.py).
               Uneven coverage, so a cell with none is *not* scored as empty.
  transit      Bus and metro departures per stop per hour, from Delhi's GTFS
               timetables (raw/gtfs/*.zip). The only input measured by hour.

``ingest_osm_poi.py`` on its own still builds a POI-only layer, as before.

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

import json
from datetime import datetime

from .base import NEUTRAL, Layer

# Multiplier applied to a cell's static POI density, by hour of day. Peaks at
# the evening commute, bottoms out in the small hours.
HOURLY_ACTIVITY: dict[int, float] = {
    0: 0.20,
    1: 0.12,
    2: 0.08,
    3: 0.08,
    4: 0.12,
    5: 0.25,
    6: 0.45,
    7: 0.65,
    8: 0.85,
    9: 0.95,
    10: 0.95,
    11: 0.95,
    12: 1.00,
    13: 1.00,
    14: 0.95,
    15: 0.95,
    16: 0.95,
    17: 1.00,
    18: 1.00,
    19: 0.95,
    20: 0.85,
    21: 0.70,
    22: 0.50,
    23: 0.32,
}


#: Residents do not leave at night the way shoppers do; a lit window and a
#: neighbour within earshot persist after the shutters come down. So the
#: population term follows the activity curve only halfway. Like the curve
#: itself, this is a judgement, not a measurement.
RESIDENT_ACTIVITY: dict[int, float] = {h: 0.5 + 0.5 * a for h, a in HOURLY_ACTIVITY.items()}


def combine_presence(
    cell_ids: list[str],
    population: dict[str, float] | None = None,
    poi: dict[str, float] | None = None,
    transit: dict[str, list[float]] | None = None,
) -> dict[str, list[float]]:
    """Per-cell, per-hour presence in [0, 1] from whichever inputs exist.

    All inputs are already normalised to [0, 1]; `transit` is per hour. Each
    becomes a term — residents, visitors, passengers — and the terms combine as

        presence = 1 - (1 - residents)(1 - visitors)(1 - passengers)

    so every source can only add people. An average was tried first and is
    wrong here: one mapped shop (POI ~0.02) averaged against a dense colony
    (population 0.6) halved the colony's score, i.e. mapping a shop made a
    street look emptier.

    Absence still means different things per input. Population and transit
    are complete, so a cell missing from them contributes nothing because
    there is nothing there. OSM POIs are not complete, and a cell with none
    contributes nothing because we do not know — which, under this rule, is
    the same arithmetic and never counts against the cell. A cell is scored
    only if at least one source speaks for it.
    """
    out: dict[str, list[float]] = {}
    for cid in cell_ids:
        has_poi = poi is not None and cid in poi
        if population is None and transit is None and not has_poi:
            continue
        hours: list[float] = []
        for h in range(24):
            absent = 1.0
            if population is not None:
                absent *= 1.0 - population.get(cid, 0.0) * RESIDENT_ACTIVITY[h]
            if has_poi:
                absent *= 1.0 - poi[cid] * HOURLY_ACTIVITY[h]
            if transit is not None and cid in transit:
                absent *= 1.0 - transit[cid][h]
            hours.append(round(Layer.clamp01(1.0 - absent), 4))
        out[cid] = hours
    return out


class CDSLayer(Layer):
    name = "cds"
    confidence = "low"

    def __init__(self) -> None:
        super().__init__()
        #: cell -> 24 hourly values, when built by build_cds.py. Takes
        #: precedence over the static value times the activity curve.
        self._hourly: dict[str, list[float]] = {}

    def load(self) -> CDSLayer:
        super().load()
        if self._loaded:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._hourly = raw.get("hourly", {})
        return self

    def save_hourly(self, hourly: dict[str, list[float]], meta: dict | None = None) -> None:
        """Persist per-hour values. The static `cells` map holds each cell's
        daily mean, so coverage and `has()` keep working unchanged."""
        daily = {cid: round(sum(v) / 24, 4) for cid, v in hourly.items()}
        self.save(daily, meta)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["hourly"] = hourly
        self.path.write_text(json.dumps(payload), encoding="utf-8")
        self._hourly = hourly

    def value(self, cell_id: str | None, when: datetime) -> float:
        if cell_id is None:
            return NEUTRAL
        hourly = self._hourly.get(cell_id)
        if hourly is not None:
            return hourly[when.hour]
        base = self._values.get(cell_id)
        if base is None:
            return NEUTRAL
        return self.clamp01(base * HOURLY_ACTIVITY.get(when.hour, 0.5))
