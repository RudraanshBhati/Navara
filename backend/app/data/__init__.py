"""Feature layers and the registry that holds them.

Layers are loaded once at application start and shared across requests. They are
read-only after load, so this is safe; the news agent writes to disk out of
band and the API reloads via ``/admin/reload`` or a restart.
"""

from __future__ import annotations

from .base import NEUTRAL, SEVERITY_WEIGHTS, Layer, normalise, severity_of
from .cds import CDSLayer
from .cip import CIPLayer
from .nsi import Incident, NSILayer
from .ntls import NTLSLayer
from .trc import TRCLayer

__all__ = [
    "Layer",
    "Incident",
    "LayerRegistry",
    "get_layers",
    "reset_layers",
    "normalise",
    "severity_of",
    "NEUTRAL",
    "SEVERITY_WEIGHTS",
]


class LayerRegistry:
    """All five layers, keyed by the name used in the weight vector."""

    def __init__(self) -> None:
        self.cip = CIPLayer()
        self.cds = CDSLayer()
        self.trc = TRCLayer()
        self.ntls = NTLSLayer()
        self.nsi = NSILayer()

    def load_all(self) -> LayerRegistry:
        for layer in self.as_dict().values():
            layer.load()
        return self

    def as_dict(self) -> dict[str, Layer]:
        return {
            "cip": self.cip,
            "cds": self.cds,
            "trc": self.trc,
            "ntls": self.ntls,
            "nsi": self.nsi,
        }

    def status(self) -> dict[str, dict]:
        """Which layers are backed by real data — surfaced by /health so a demo
        never silently runs on five neutral placeholders."""
        return {
            name: {
                "available": layer.available,
                "confidence": layer.confidence,
                "cells_covered": layer.coverage,
                "meta": layer.meta,
            }
            for name, layer in self.as_dict().items()
        }


_registry: LayerRegistry | None = None


def get_layers() -> LayerRegistry:
    global _registry
    if _registry is None:
        _registry = LayerRegistry().load_all()
    return _registry


def reset_layers() -> None:
    """Force a reload on next access. Used after an agent run and by tests."""
    global _registry
    _registry = None
