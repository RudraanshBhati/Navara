"""The news agent, as a LangGraph state machine.

    fetch -> dedupe -> prefilter -> extract -> geocode -> merge -> persist

Modelling this as a graph rather than a script buys three things that matter
here:

  1. Each step is independently testable with a hand-built state dict, and the
     expensive steps (extract, geocode) can be swapped for fakes without
     touching the others.
  2. The state carries `stats` and `errors` through, so a run that produced
     nothing tells you *which* stage it died at — the difference between "no
     news today" and "the RSS feeds moved" is invisible otherwise.
  3. Adding a human-review gate before `persist` later is an edge change, not a
     rewrite. That gate is likely: this layer moves a map that people navigate
     by, on the strength of a model reading a headline.

Run it with `python scripts/run_news_agent.py`.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import get_settings
from ..data.base import severity_of
from ..data.nsi import Incident, NSILayer
from .extract import ExtractedIncident, extract_batch
from .geocode import Geocoder
from .sources import Article, NewsSource, default_sources

log = logging.getLogger(__name__)

#: Title-token overlap above which two articles are treated as the same event.
#: Tuned down from 0.6 against real headline paraphrase: outlets rewrite the
#: verb and drop a clause ("Woman robbed at knifepoint near Saket Metro station"
#: vs "Knifepoint robbery reported near Saket Metro station" overlap only 0.56),
#: so a stricter threshold lets syndicated coverage through as distinct events
#: and multiplies one incident's effect on the map.
DUPLICATE_THRESHOLD = 0.5


def _merge_stats(left: dict, right: dict) -> dict:
    return {**left, **right}


class NewsAgentState(TypedDict, total=False):
    lookback_hours: int
    dry_run: bool

    articles: list[Article]
    extracted: list[tuple[Article, ExtractedIncident]]
    incidents: list[Incident]

    stats: Annotated[dict, _merge_stats]
    errors: list[str]


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def node_fetch(state: NewsAgentState, sources: list[NewsSource] | None = None) -> dict:
    lookback = state.get("lookback_hours") or get_settings().news_lookback_hours
    srcs = sources or default_sources()

    articles: list[Article] = []
    errors: list[str] = []
    per_source: dict[str, int] = {}

    for src in srcs:
        try:
            got = src.fetch(lookback)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{src.name}: {exc}")
            continue
        per_source[src.name] = len(got)
        articles.extend(got)

    log.info("fetched %d articles from %d sources", len(articles), len(srcs))
    return {
        "articles": articles,
        "errors": state.get("errors", []) + errors,
        "stats": {"fetched": len(articles), "per_source": per_source},
    }


def node_dedupe(state: NewsAgentState) -> dict:
    """Collapse the same event reported by several outlets.

    Five papers covering one snatching should move the map once, not five
    times. Without this the NSI score becomes a measure of how newsworthy an
    incident was rather than how dangerous a place is — and the loudest stories
    are exactly the ones that get syndicated most.
    """
    articles = state.get("articles", [])
    seen_urls: set[str] = set()
    kept: list[Article] = []
    fingerprints: list[set[str]] = []

    # Most recent first, so the survivor of a duplicate group is the freshest.
    for article in sorted(articles, key=lambda a: a.published_at, reverse=True):
        if not article.title:
            continue
        if article.url in seen_urls:
            continue
        seen_urls.add(article.url)

        fp = _fingerprint(article.title)
        if any(_jaccard(fp, prev) >= DUPLICATE_THRESHOLD for prev in fingerprints):
            continue
        fingerprints.append(fp)
        kept.append(article)

    log.info("deduped %d -> %d articles", len(articles), len(kept))
    return {"articles": kept, "stats": {"after_dedupe": len(kept)}}


def node_prefilter(state: NewsAgentState) -> dict:
    """Drop obviously irrelevant articles before spending LLM calls."""
    articles = state.get("articles", [])
    kept = [a for a in articles if a.looks_relevant()]
    log.info("prefilter kept %d/%d articles", len(kept), len(articles))
    return {"articles": kept, "stats": {"after_prefilter": len(kept)}}


def node_extract(state: NewsAgentState) -> dict:
    articles = state.get("articles", [])
    if not articles:
        return {"extracted": [], "stats": {"extracted": 0}}

    try:
        paired = extract_batch(articles)
    except Exception as exc:  # noqa: BLE001
        log.error("extraction stage failed: %s", exc)
        return {
            "extracted": [],
            "errors": state.get("errors", []) + [f"extract: {exc}"],
            "stats": {"extracted": 0},
        }

    incidents_only = [
        (a, e)
        for a, e in paired
        if e.is_safety_incident and e.location_text.strip()
    ]
    log.info("%d of %d extractions are located incidents", len(incidents_only), len(paired))
    return {
        "extracted": incidents_only,
        "stats": {"extracted": len(paired), "located_incidents": len(incidents_only)},
    }


def node_geocode(state: NewsAgentState) -> dict:
    extracted = state.get("extracted", [])
    if not extracted:
        return {"incidents": [], "stats": {"geocoded": 0}}

    geocoder = Geocoder()
    incidents: list[Incident] = []
    misses = 0

    for article, ex in extracted:
        geo = geocoder.geocode(ex.location_text)
        if geo is None:
            misses += 1
            continue

        occurred = _resolve_occurred_at(ex.occurred_at, article.published_at)
        severity = severity_of(ex.category)
        # An incident after dark says more about a route at 11pm than the same
        # incident at noon, and it is also the case this product exists for.
        if ex.night_time:
            severity = min(1.0, severity * 1.15)

        incidents.append(
            Incident(
                id=_incident_id(article.url, ex.location_text, occurred),
                lat=geo.lat,
                lon=geo.lon,
                occurred_at=occurred.isoformat(),
                severity=severity,
                category=ex.category,
                headline=article.title,
                url=article.url,
                source=article.source,
                # Two independent uncertainties compound: how sure the model is
                # that this is a real located incident, and how precisely the
                # geocoder pinned the place it named.
                confidence=round(ex.confidence * geo.precision_factor, 3),
                location_text=geo.formatted,
                note=ex.note,
                tags=["night"] if ex.night_time else [],
            )
        )

    geocoder.save_cache()
    log.info("geocoded %d incidents (%d misses)", len(incidents), misses)
    return {
        "incidents": incidents,
        "stats": {"geocoded": len(incidents), "geocode_misses": misses},
    }


def node_merge(state: NewsAgentState) -> dict:
    """Union this run's incidents with the stored set, and drop expired ones.

    Each run is a partial view — sources only look back a couple of days — so
    replacing the store rather than merging would make the map forget last
    week entirely. Dedupe is by incident id, which is derived from the article
    URL, so re-fetching the same story is idempotent.
    """
    settings = get_settings()
    fresh = state.get("incidents", [])

    existing = NSILayer().load().incidents
    by_id: dict[str, Incident] = {i.id: i for i in existing}
    added = sum(1 for i in fresh if i.id not in by_id)
    for inc in fresh:
        by_id[inc.id] = inc  # re-extraction wins; it may have better data

    cutoff = datetime.now(UTC) - timedelta(days=settings.news_max_age_days)
    live = [i for i in by_id.values() if i.occurred_dt >= cutoff]
    expired = len(by_id) - len(live)

    live.sort(key=lambda i: i.occurred_dt, reverse=True)
    log.info(
        "merged: %d existing + %d new -> %d live (%d expired out)",
        len(existing), added, len(live), expired,
    )
    return {
        "incidents": live,
        "stats": {"new_incidents": added, "expired": expired, "total_live": len(live)},
    }


def node_persist(state: NewsAgentState) -> dict:
    if state.get("dry_run"):
        log.info("dry run — not writing %d incidents", len(state.get("incidents", [])))
        return {"stats": {"persisted": 0, "dry_run": True}}

    incidents = state.get("incidents", [])
    layer = NSILayer()
    layer.save_incidents(
        incidents,
        meta={
            "run_at": datetime.now(UTC).isoformat(),
            "stats": state.get("stats", {}),
            "errors": state.get("errors", []),
        },
    )
    log.info("persisted %d incidents to %s", len(incidents), layer.path)
    return {"stats": {"persisted": len(incidents)}}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------


def _has_articles(state: NewsAgentState) -> str:
    return "extract" if state.get("articles") else "merge"


def build_graph(sources: list[NewsSource] | None = None):
    """Compile the agent. Pass `sources` to inject fakes in tests."""
    g = StateGraph(NewsAgentState)

    g.add_node("fetch", lambda s: node_fetch(s, sources))
    g.add_node("dedupe", node_dedupe)
    g.add_node("prefilter", node_prefilter)
    g.add_node("extract", node_extract)
    g.add_node("geocode", node_geocode)
    g.add_node("merge", node_merge)
    g.add_node("persist", node_persist)

    g.add_edge(START, "fetch")
    g.add_edge("fetch", "dedupe")
    g.add_edge("dedupe", "prefilter")
    # Skip the paid stages entirely when there is nothing to process, but still
    # run merge/persist so expired incidents age out on a quiet day.
    g.add_conditional_edges("prefilter", _has_articles, {"extract": "extract", "merge": "merge"})
    g.add_edge("extract", "geocode")
    g.add_edge("geocode", "merge")
    g.add_edge("merge", "persist")
    g.add_edge("persist", END)

    return g.compile()


def run_agent(
    lookback_hours: int | None = None,
    dry_run: bool = False,
    sources: list[NewsSource] | None = None,
) -> NewsAgentState:
    graph = build_graph(sources)
    initial: NewsAgentState = {
        "lookback_hours": lookback_hours or get_settings().news_lookback_hours,
        "dry_run": dry_run,
        "articles": [],
        "extracted": [],
        "incidents": [],
        "stats": {},
        "errors": [],
    }
    return graph.invoke(initial)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "a", "an", "the", "in", "on", "at", "of", "for", "to", "and", "with",
    "after", "over", "from", "by", "as", "is", "was", "says", "said", "delhi",
}


def _fingerprint(title: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _resolve_occurred_at(stated: str, published: datetime) -> datetime:
    """Prefer the date the model read out of the article, fall back to publication."""
    if stated:
        try:
            dt = datetime.fromisoformat(stated)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            pass
    return published if published.tzinfo else published.replace(tzinfo=UTC)


def _incident_id(url: str, location: str, occurred: datetime) -> str:
    raw = f"{url}|{location.lower()}|{occurred.date().isoformat()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
