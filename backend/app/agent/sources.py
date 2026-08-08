"""Where the news agent gets its raw material.

Three sources, deliberately layered by cost:

  GDELT   free, no key, global news index with a 15-minute update cadence.
          The default and the workhorse.
  RSS     free, no key, straight from Delhi city desks. Higher signal density
          than GDELT for local incidents because the feeds are already
          city-scoped, but shallow history — RSS gives you the last ~50 items,
          not a date range.
  NewsAPI optional, needs a key. Better structured metadata; the free tier is
          rate-limited and delays articles by 24h, which blunts the whole point
          of this layer. Off unless a key is present.

Every source returns the same `Article` shape so the rest of the graph does not
care where a story came from.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import feedparser
import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
NEWSAPI_URL = "https://newsapi.org/v2/everything"

#: Delhi city-desk RSS feeds. Add to this list freely — the pipeline dedupes.
DELHI_RSS_FEEDS: list[tuple[str, str]] = [
    ("TOI Delhi", "https://timesofindia.indiatimes.com/rssfeeds/-2128839596.cms"),
    (
        "Hindustan Times Delhi",
        "https://www.hindustantimes.com/feeds/rss/cities/delhi-news/rssfeed.xml",
    ),
    ("Indian Express Delhi", "https://indianexpress.com/section/cities/delhi/feed/"),
    ("NDTV Cities", "https://feeds.feedburner.com/ndtvnews-cities"),
]

#: Terms that make an article worth spending an LLM call on. This is a cheap
#: pre-filter, not the real classifier — the extraction step does the actual
#: judging. Keep it generous: a false positive costs one API call, a false
#: negative loses an incident entirely.
SAFETY_TERMS = [
    "assault",
    "molest",
    "harass",
    "stalk",
    "rape",
    "abduct",
    "kidnap",
    "snatch",
    "robbery",
    "rob ",
    "chain snatching",
    "mugg",
    "stab",
    "murder",
    "attack",
    "eve teasing",
    "groping",
    "crime",
    "arrested",
    "accused",
    "police",
    "fir ",
    "victim",
    "woman",
    "girl",
    "unsafe",
    "dark stretch",
    "street light",
    "streetlight",
]


@dataclass
class Article:
    """A raw news item, before any extraction."""

    title: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""

    @property
    def text(self) -> str:
        return f"{self.title}\n\n{self.summary}".strip()

    def looks_relevant(self) -> bool:
        blob = self.text.lower()
        return any(term in blob for term in SAFETY_TERMS)


class NewsSource(ABC):
    name: str

    @abstractmethod
    def fetch(self, lookback_hours: int) -> list[Article]:
        """Return recent articles. Must not raise — a dead source should degrade
        the run, not fail it."""


class GDELTSource(NewsSource):
    name = "gdelt"

    def fetch(self, lookback_hours: int) -> list[Article]:
        params = {
            # GDELT's query language: quoted phrase + a location filter. The
            # sourcecountry filter keeps out the steady trickle of "Delhi" in
            # foreign wire copy about national politics.
            "query": "(Delhi) (crime OR assault OR robbery OR snatching OR harassment) sourcecountry:india",
            "mode": "ArtList",
            "maxrecords": "150",
            "format": "json",
            "timespan": f"{max(1, lookback_hours)}h",
            "sort": "datedesc",
        }
        try:
            r = httpx.get(GDELT_URL, params=params, timeout=30.0)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001 - source must degrade gracefully
            log.warning("GDELT fetch failed: %s", exc)
            return []

        out: list[Article] = []
        for item in payload.get("articles", []):
            published = _parse_gdelt_date(item.get("seendate", ""))
            if published is None:
                continue
            out.append(
                Article(
                    title=item.get("title", "").strip(),
                    url=item.get("url", ""),
                    source=item.get("domain", "gdelt"),
                    published_at=published,
                )
            )
        log.info("GDELT returned %d articles", len(out))
        return out


class RSSSource(NewsSource):
    name = "rss"

    def __init__(self, feeds: list[tuple[str, str]] | None = None) -> None:
        self.feeds = feeds or DELHI_RSS_FEEDS

    def fetch(self, lookback_hours: int) -> list[Article]:
        cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
        out: list[Article] = []

        for source_name, url in self.feeds:
            try:
                parsed = feedparser.parse(url)
            except Exception as exc:  # noqa: BLE001
                log.warning("RSS fetch failed for %s: %s", source_name, exc)
                continue

            for entry in parsed.entries:
                published = _parse_feed_date(entry)
                # A feed item with no parseable date is kept: RSS dates are
                # frequently malformed, and dropping the item loses a real
                # story. The extraction step will try to date it from the text.
                if published is not None and published < cutoff:
                    continue
                out.append(
                    Article(
                        title=getattr(entry, "title", "").strip(),
                        url=getattr(entry, "link", ""),
                        source=source_name,
                        published_at=published or datetime.now(UTC),
                        summary=_strip_html(getattr(entry, "summary", "")),
                    )
                )
        log.info("RSS returned %d articles across %d feeds", len(out), len(self.feeds))
        return out


class NewsAPISource(NewsSource):
    name = "newsapi"

    def fetch(self, lookback_hours: int) -> list[Article]:
        key = get_settings().newsapi_key
        if not key:
            return []

        since = datetime.now(UTC) - timedelta(hours=lookback_hours)
        params = {
            "q": "Delhi AND (crime OR assault OR robbery OR snatching OR harassment)",
            "from": since.strftime("%Y-%m-%dT%H:%M:%S"),
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": "100",
        }
        try:
            r = httpx.get(NEWSAPI_URL, params=params, headers={"X-Api-Key": key}, timeout=30.0)
            r.raise_for_status()
            payload = r.json()
        except Exception as exc:  # noqa: BLE001
            log.warning("NewsAPI fetch failed: %s", exc)
            return []

        out: list[Article] = []
        for item in payload.get("articles", []):
            published = _parse_iso(item.get("publishedAt"))
            if published is None:
                continue
            out.append(
                Article(
                    title=(item.get("title") or "").strip(),
                    url=item.get("url", ""),
                    source=(item.get("source") or {}).get("name", "newsapi"),
                    published_at=published,
                    summary=(item.get("description") or "").strip(),
                )
            )
        log.info("NewsAPI returned %d articles", len(out))
        return out


def default_sources() -> list[NewsSource]:
    """The sources a normal run uses. NewsAPI opts itself in via its key."""
    sources: list[NewsSource] = [GDELTSource(), RSSSource()]
    if get_settings().newsapi_key:
        sources.append(NewsAPISource())
    return sources


# ---------------------------------------------------------------------------
# Date parsing. Every feed does it differently and half of them do it wrong.
# ---------------------------------------------------------------------------


def _parse_gdelt_date(value: str) -> datetime | None:
    """GDELT uses a compact 'YYYYMMDDTHHMMSSZ' stamp."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_feed_date(entry) -> datetime | None:
    import calendar

    for attr in ("published_parsed", "updated_parsed"):
        parsed = getattr(entry, attr, None)
        if parsed:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
    return None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return None


def _strip_html(text: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", text or "").strip()
