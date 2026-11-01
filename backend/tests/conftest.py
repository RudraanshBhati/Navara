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
def _clean_globals(tmp_path, monkeypatch):
    """Also pins the street-level terms to *no data*: the road network and map
    places live in data/processed/ like the layers, and a test that scored a
    route against them would pass or fail depending on the machine."""
    import app.features as features_mod
    import app.pois as pois_mod
    from app.features import reset_features

    monkeypatch.setattr(features_mod, "ROADS_PATH", tmp_path / "no_roads.json")
    monkeypatch.setattr(features_mod, "CAMERAS_PATH", tmp_path / "no_cameras.json")
    monkeypatch.setattr(features_mod, "SIGNALS_PATH", tmp_path / "no_signals.json")
    monkeypatch.setattr(features_mod, "POPULATION_PATH", tmp_path / "no_population.json")
    monkeypatch.setattr(pois_mod, "_index", pois_mod.POIIndex([]))
    reset_grid()
    reset_layers()
    reset_features()
    yield
    reset_grid()
    reset_layers()
    reset_features()


@pytest.fixture(autouse=True)
def _no_article_fetching(monkeypatch):
    """Never let a test reach out to a newsroom's server.

    `node_enrich` fetches article bodies for anything clearing the prefilter,
    so a test that happens to build a relevant-looking article would silently
    start making real HTTP requests — slow, flaky, and rude. Tests that want
    the fetch path exercise it against fakes and opt in explicitly.
    """
    import app.config as config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "fetch_article_bodies", False, raising=False)
    yield


@pytest.fixture(autouse=True)
def _no_real_archive(tmp_path, monkeypatch):
    """Never let a test append to the real news archive or its logs.

    Any test that runs the whole graph reaches `node_persist`, which appends to
    all three. Those files cannot be regenerated, and a test row in them is
    indistinguishable from a real incident a year later.
    """
    import app.agent.archive as archive_mod

    archive_dir = tmp_path / "archive"
    monkeypatch.setattr(archive_mod, "ARCHIVE_PATH", archive_dir / "nsi_archive.jsonl")
    monkeypatch.setattr(archive_mod, "REVIEW_LOG_PATH", archive_dir / "news_reviewed.jsonl")
    monkeypatch.setattr(archive_mod, "RUN_LOG_PATH", archive_dir / "agent_runs.jsonl")
    yield


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


@pytest.fixture
def archive_path(tmp_path, monkeypatch):
    """Redirect the news archive at a temp file.

    Both module globals have to move: `archive.ARCHIVE_PATH` is what the
    functions default to, and a test that only patched one would quietly write
    into the developer's real archive — the one file in this project that
    cannot be regenerated.
    """
    import app.agent.archive as archive_mod

    path = tmp_path / "nsi_archive.jsonl"
    monkeypatch.setattr(archive_mod, "ARCHIVE_PATH", path)
    return path


def write_layer(path, name: str, cells: dict[str, float]) -> None:
    path.write_text(json.dumps({"layer": name, "meta": {}, "cells": cells}), encoding="utf-8")
