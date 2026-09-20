"""The permanent incident archive.

What is actually under test is that data stops being lost. The NSI layer is a
30-day window by design; before the archive existed, an incident falling out of
that window was gone for good, taking with it the only incident-level Delhi
crime record this project can obtain. These tests pin the two halves of the
fix: everything that passes through the window is kept, and keeping it is
idempotent enough to run daily for years.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.agent.archive import append_incidents, archive_stats, load_archive
from app.data.nsi import Incident


def _incident(inc_id: str, days_ago: float = 1, **overrides) -> Incident:
    base = {
        "id": inc_id,
        "lat": 28.6139,
        "lon": 77.2090,
        "occurred_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        "severity": 0.9,
        "category": "assault",
        "headline": f"Incident {inc_id}",
        "url": f"https://example.test/{inc_id}",
        "source": "fixture",
        "confidence": 0.8,
    }
    return Incident(**{**base, **overrides})


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_incidents_survive_a_write_and_read(archive_path):
    append_incidents([_incident("a"), _incident("b")], archive_path)
    back = load_archive(archive_path)

    assert set(back) == {"a", "b"}
    assert back["a"].category == "assault"


def test_reading_a_missing_archive_is_empty_not_an_error(archive_path):
    assert load_archive(archive_path) == {}
    assert archive_stats(archive_path)["incidents"] == 0


def test_appending_nothing_does_not_create_a_file(archive_path):
    append_incidents([], archive_path)
    assert not archive_path.exists()


# ---------------------------------------------------------------------------
# Idempotence — this is what makes a daily cron job safe
# ---------------------------------------------------------------------------


def test_rewriting_the_same_incident_appends_nothing(archive_path):
    incidents = [_incident("a"), _incident("b")]
    append_incidents(incidents, archive_path)
    first = archive_path.read_text(encoding="utf-8")

    stats = append_incidents(incidents, archive_path)

    assert archive_path.read_text(encoding="utf-8") == first
    assert stats == {"archived_new": 0, "archived_updated": 0, "archive_total": 2}


def test_a_changed_incident_is_appended_and_the_newest_wins(archive_path):
    append_incidents([_incident("a", confidence=0.4)], archive_path)
    stats = append_incidents([_incident("a", confidence=0.9)], archive_path)

    assert stats["archived_updated"] == 1
    assert len(archive_path.read_text(encoding="utf-8").strip().splitlines()) == 2
    assert load_archive(archive_path)["a"].confidence == 0.9


def test_a_cell_id_assigned_at_index_time_is_not_a_change(archive_path):
    """NSILayer._build_index mutates cell_id in place.

    If the fingerprint counted it, every incident would be rewritten on every
    run forever and the log would grow without bound.
    """
    incident = _incident("a")
    append_incidents([incident], archive_path)

    stats = append_incidents([replace(incident, cell_id="r0046c0072")], archive_path)

    assert stats["archived_new"] == 0
    assert stats["archived_updated"] == 0


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------


def test_a_corrupt_line_does_not_destroy_the_history(archive_path):
    """An interrupted write costs its own line, not months of accumulation."""
    append_incidents([_incident("a"), _incident("b")], archive_path)
    with archive_path.open("a", encoding="utf-8") as fh:
        fh.write('{"id": "truncated", "lat":\n')
    append_incidents([_incident("c")], archive_path)

    back = load_archive(archive_path)
    assert set(back) == {"a", "b", "c"}


def test_blank_lines_are_ignored(archive_path):
    append_incidents([_incident("a")], archive_path)
    with archive_path.open("a", encoding="utf-8") as fh:
        fh.write("\n\n")

    assert set(load_archive(archive_path)) == {"a"}


def test_each_record_is_one_line(archive_path):
    """JSON Lines, not pretty-printed JSON — appends must not need a rewrite."""
    append_incidents([_incident("a"), _incident("b")], archive_path)
    lines = archive_path.read_text(encoding="utf-8").strip().splitlines()

    assert len(lines) == 2
    for line in lines:
        json.loads(line)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def test_stats_report_the_accumulated_span(archive_path):
    append_incidents(
        [_incident("old", days_ago=100), _incident("new", days_ago=0)],
        archive_path,
    )
    stats = archive_stats(archive_path)

    assert stats["incidents"] == 2
    assert stats["days_covered"] == 101  # inclusive of both ends


# ---------------------------------------------------------------------------
# The graph: what the archive was built to stop losing
# ---------------------------------------------------------------------------


def test_merge_keeps_expired_incidents_for_the_archive(processed_dir, archive_path, monkeypatch):
    """The whole point. An incident too old for NSI is still crime data.

    node_merge drops it from the live window — correct, NSI is about what
    happened recently — but it must still reach the archive, because CIP has no
    other source.
    """
    import app.data.nsi as nsi_mod

    monkeypatch.setattr(nsi_mod, "INCIDENTS_PATH", processed_dir / "nsi_incidents.json")

    from app.agent.graph import node_merge

    recent = _incident("recent", days_ago=2)
    ancient = _incident("ancient", days_ago=400)

    out = node_merge({"incidents": [recent, ancient]})

    assert [i.id for i in out["incidents"]] == ["recent"]
    assert {i.id for i in out["archivable"]} == {"recent", "ancient"}


def test_persist_writes_both_the_window_and_the_archive(processed_dir, archive_path, monkeypatch):
    import app.data.nsi as nsi_mod

    monkeypatch.setattr(nsi_mod, "INCIDENTS_PATH", processed_dir / "nsi_incidents.json")

    from app.agent.graph import node_persist

    live = _incident("live", days_ago=1)
    expired = _incident("expired", days_ago=400)

    stats = node_persist({"incidents": [live], "archivable": [live, expired]})["stats"]

    assert stats["persisted"] == 1
    assert stats["archived_new"] == 2
    assert set(load_archive(archive_path)) == {"live", "expired"}


def test_a_dry_run_writes_no_archive(archive_path):
    from app.agent.graph import node_persist

    node_persist({"dry_run": True, "incidents": [_incident("a")], "archivable": [_incident("a")]})

    assert not archive_path.exists()


def test_an_unwritable_archive_does_not_fail_the_run(processed_dir, monkeypatch):
    """The live layer is already saved and serving; the next run re-appends."""
    import app.agent.archive as archive_mod
    import app.data.nsi as nsi_mod

    monkeypatch.setattr(nsi_mod, "INCIDENTS_PATH", processed_dir / "nsi_incidents.json")

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(archive_mod, "append_incidents", boom)
    monkeypatch.setattr("app.agent.graph.append_incidents", boom)

    from app.agent.graph import node_persist

    out = node_persist({"incidents": [_incident("a")], "archivable": [_incident("a")]})

    assert out["stats"]["persisted"] == 1
    assert any("archive" in e for e in out["errors"])
