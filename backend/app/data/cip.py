"""CIP — Crime Incidence Profile.

Per-cell crime density, inverted so that 1 = low reported crime.

Built by ``scripts/ingest_crime_data.py``. See docs/DATA_SOURCES.md for the
sourcing decision — in particular the distinction between district-level annual
aggregates (which cannot support the TRC layer) and incident-level records with
timestamps (which can).

Optional refinement, not yet wired: weight incidents by offence severity rather
than counting them equally. Several papers in docs/RESEARCH.md do this and
report a real accuracy gain. Ranking a pickpocketing equal to an assault is the
single most questionable simplification in the current layer.
"""

from __future__ import annotations

from .base import StaticLayer


class CIPLayer(StaticLayer):
    name = "cip"
    confidence = "high"
