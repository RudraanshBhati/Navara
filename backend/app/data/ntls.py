"""NTLS — Night-Time Lighting Score.

Per-cell night radiance from VIIRS (VNP46A2 "Black Marble" daily corrected
reflectance), normalised so 1 = well lit. Not inverted: brighter is safer.

Built by ``scripts/ingest_viirs.py``.

This is the best-evidenced of the four layers — the lighting/crime link is
established in the literature with satellite-derived radiance specifically (see
docs/RESEARCH.md). If we end up tuning weights, this one has the strongest case
for carrying more than an equal share.

Caveat worth remembering when reading explanations: VIIRS measures light
*leaving* the ground, which includes commercial signage and headlights, not just
street lighting. A bright cell is not necessarily a well-lit footpath. OSM
``lit=yes/no`` way tags are the intended secondary signal to disambiguate this;
they are not yet ingested.
"""

from __future__ import annotations

from datetime import datetime

from .base import NEUTRAL, StaticLayer

# Daylight hours where lighting is irrelevant to safety. Delhi does not observe
# DST and sits at the eastern edge of IST, so sunrise/sunset move by roughly an
# hour across the year; these bounds are deliberately conservative.
DAY_START_HOUR = 7
DAY_END_HOUR = 18


class NTLSLayer(StaticLayer):
    name = "ntls"
    confidence = "high"

    def value(self, cell_id: str | None, when: datetime) -> float:
        """Lighting only counts against a route after dark.

        In daylight a dim cell is not a hazard, and letting NTLS drag down
        daytime scores would produce explanations that read as nonsense to the
        user ("poorly lit stretch" at 2pm).
        """
        if DAY_START_HOUR <= when.hour < DAY_END_HOUR:
            return 1.0
        if cell_id is None:
            return NEUTRAL
        return self._values.get(cell_id, NEUTRAL)
