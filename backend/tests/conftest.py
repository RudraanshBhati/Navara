"""Shared fixtures.

Tests never touch the network, never need an API key, and never depend on
whether the data layers happen to be built on the machine running them. That
last one is the fiddly part: layers load from `data/processed/`, which is
populated on a developer's machine and empty in CI, so anything reading the
global registry would pass locally and fail in CI (or worse, the reverse).
Every fixture here pins the state explicitly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from app.data import LayerRegistry, reset_layers
from app.data.nsi import Incident
from app.grid import Grid, reset_grid


@pytest.fixture(autouse=True)
def _clean_globals():
    reset_grid()
    reset_layers()
    yield
    reset_grid()
    reset_layers()


@pytest.fixture
def grid() -> Grid:
    return Grid()


@pytest.fixture
def empty_layers() -> LayerRegistry:
    """A registry with no data loaded — every layer returns NEUTRAL."""
    return LayerRegistry()


@pytest.fixture
def layers(grid: Grid) -> LayerRegistry:
    """A registry with hand-built values, so assertions can be exact.

    Two neighbouring cells in central Delhi: one deliberately good on every
    layer, one deliberately bad. Anything that cannot tell these apart is
    broken.
    """
    reg = LayerRegistry()

    good = grid.cell_id(28.6139, 77.2090)  # Connaught Place-ish
    bad = grid.cell_id(28.6100, 77.2200)

    reg.cip._values = {good: 0.9, bad: 0.1}
    reg.cip._loaded = True
    reg.cds._values = {good: 0.9, bad: 0.2}
    reg.cds._loaded = True
    reg.ntls._values = {good: 0.95, bad: 0.1}
    reg.ntls._loaded = True

    reg.trc._global_curve = [0.4] * 6 + [0.9] * 12 + [0.5] * 6
    reg.trc._loaded = True

    return reg


@pytest.fixture
def night() -> datetime:
    return datetime(2026, 3, 15, 23, 30, tzinfo=UTC)


@pytest.fixture
def midday() -> datetime:
    return datetime(2026, 3, 15, 13, 0, tzinfo=UTC)


@pytest.fixture
def sample_incident() -> Incident:
    return Incident(
        id="test0001",
        lat=28.6139,
        lon=77.2090,
        occurred_at=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        severity=0.9,
        category="assault",
        headline="Test incident near Connaught Place",
        url="https://example.test/1",
        source="fixture",
        confidence=1.0,
    )


@pytest.fixture
def processed_dir(tmp_path, monkeypatch):
    """Redirect layer persistence at a temp directory."""
    import app.config as config
    import app.data.base as base

    monkeypatch.setattr(config, "PROCESSED_DIR", tmp_path)
    monkeypatch.setattr(base, "PROCESSED_DIR", tmp_path)
    return tmp_path


def write_layer(path, name: str, cells: dict[str, float]) -> None:
    path.write_text(json.dumps({"layer": name, "meta": {}, "cells": cells}), encoding="utf-8")
