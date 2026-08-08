"""News agent graph wiring. No network, no LLM, no keys.

The expensive nodes (extract, geocode) are exercised through fakes; what is
actually under test is the graph — that a quiet day still ages incidents out,
that duplicate coverage does not multiply a single event, and that the
prefilter does not throw away real incidents.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agent.graph import (
    _fingerprint,
    _incident_id,
    _jaccard,
    _resolve_occurred_at,
    node_dedupe,
    node_prefilter,
)
from app.agent.sources import Article, NewsSource


def _article(title: str, url: str, hours_ago: float = 1, summary: str = "") -> Article:
    return Article(
        title=title,
        url=url,
        source="fixture",
        published_at=datetime.now(UTC) - timedelta(hours=hours_ago),
        summary=summary,
    )


class FakeSource(NewsSource):
    name = "fake"

    def __init__(self, articles: list[Article]) -> None:
        self.articles = articles

    def fetch(self, lookback_hours: int) -> list[Article]:  # noqa: ARG002
        return self.articles


class BrokenSource(NewsSource):
    name = "broken"

    def fetch(self, lookback_hours: int) -> list[Article]:  # noqa: ARG002
        raise RuntimeError("feed is down")


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------


def test_a_broken_source_does_not_kill_the_run():
    from app.agent.graph import node_fetch

    state = node_fetch(
        {"lookback_hours": 24, "errors": []},
        [BrokenSource(), FakeSource([_article("Robbery near Saket", "u1")])],
    )
    assert len(state["articles"]) == 1
    assert any("broken" in e for e in state["errors"])


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


def test_the_same_story_from_five_outlets_counts_once():
    """Otherwise NSI measures newsworthiness, not danger."""
    articles = [
        _article("Woman robbed at knifepoint near Saket Metro station", "u1"),
        _article("Woman robbed at knifepoint near Saket Metro", "u2"),
        _article("Knifepoint robbery reported near Saket Metro station", "u3"),
    ]
    out = node_dedupe({"articles": articles})
    assert len(out["articles"]) == 1


def test_distinct_incidents_survive_dedupe():
    articles = [
        _article("Woman robbed at knifepoint near Saket Metro station", "u1"),
        _article("Chain snatching reported in Karol Bagh market", "u2"),
    ]
    out = node_dedupe({"articles": articles})
    assert len(out["articles"]) == 2


def test_dedupe_keeps_the_freshest_copy():
    articles = [
        _article("Robbery near Saket Metro station reported", "old", hours_ago=20),
        _article("Robbery near Saket Metro station reported", "new", hours_ago=1),
    ]
    out = node_dedupe({"articles": articles})
    assert len(out["articles"]) == 1
    assert out["articles"][0].url == "new"


def test_identical_urls_collapse():
    out = node_dedupe({"articles": [_article("A", "same"), _article("B", "same")]})
    assert len(out["articles"]) == 1


# ---------------------------------------------------------------------------
# prefilter
# ---------------------------------------------------------------------------


def test_prefilter_keeps_safety_stories_and_drops_the_rest():
    articles = [
        _article("Woman harassed near Lajpat Nagar market", "u1"),
        _article("Delhi Metro announces new timetable", "u2"),
        _article("Chain snatching in Karol Bagh", "u3"),
        _article("Monsoon expected next week", "u4"),
    ]
    out = node_prefilter({"articles": articles})
    kept = {a.url for a in out["articles"]}

    assert "u1" in kept and "u3" in kept
    assert "u2" not in kept and "u4" not in kept


def test_prefilter_reads_the_summary_not_just_the_title():
    article = _article(
        "Late night incident in south Delhi",
        "u1",
        summary="A woman was assaulted while walking home, police said.",
    )
    assert node_prefilter({"articles": [article]})["articles"] == [article]


# ---------------------------------------------------------------------------
# graph shape
# ---------------------------------------------------------------------------


def test_quiet_day_skips_extraction_but_still_runs(processed_dir, monkeypatch):
    """Nothing in the news must still age old incidents out of the layer."""
    import app.data.nsi as nsi_mod

    monkeypatch.setattr(nsi_mod, "INCIDENTS_PATH", processed_dir / "nsi_incidents.json")

    from app.agent.graph import run_agent

    state = run_agent(sources=[FakeSource([_article("Metro timetable update", "u1")])])

    assert state["stats"]["after_prefilter"] == 0
    assert state["stats"].get("extracted", 0) == 0
    assert "persisted" in state["stats"]


def test_graph_compiles_with_every_node_reachable():
    from app.agent.graph import build_graph

    graph = build_graph([FakeSource([])])
    nodes = set(graph.get_graph().nodes)
    for expected in ("fetch", "dedupe", "prefilter", "extract", "geocode", "merge", "persist"):
        assert expected in nodes


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_incident_id_is_stable_and_distinct():
    when = datetime(2026, 3, 15, tzinfo=UTC)
    a = _incident_id("https://x/1", "Saket", when)
    b = _incident_id("https://x/1", "saket", when)  # case-insensitive
    c = _incident_id("https://x/2", "Saket", when)

    assert a == b
    assert a != c
    assert len(a) == 16


def test_occurred_at_prefers_the_stated_date():
    published = datetime(2026, 3, 15, tzinfo=UTC)
    assert _resolve_occurred_at("2026-03-12", published).day == 12
    assert _resolve_occurred_at("", published) == published
    assert _resolve_occurred_at("not a date", published) == published


def test_occurred_at_always_returns_something_timezone_aware():
    naive = datetime(2026, 3, 15)
    assert _resolve_occurred_at("", naive).tzinfo is not None


def test_fingerprint_drops_stopwords_and_city_name():
    fp = _fingerprint("A woman was robbed in Delhi at the market")
    assert "delhi" not in fp
    assert "the" not in fp
    assert "robbed" in fp


def test_jaccard_bounds():
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert 0 < _jaccard({"a", "b"}, {"b", "c"}) < 1
