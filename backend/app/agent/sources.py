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
import time
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
    #: The article itself, filled in after the prefilter by agent/fetch.py.
    #: Empty when the page could not be read — paywall, consent wall, bot block.
    body: str = ""

    @property
    def text(self) -> str:
        """Everything known about this article, for the prefilter and the model.

        The body is included once fetched, which is the difference between the
        extractor reading a street name and guessing one. It is deliberately
        last: title and summary are the most reliable parts, so they survive
        any truncation downstream.
        """
        parts = [self.title, self.summary, self.body]
        return "\n\n".join(p for p in parts if p and p.strip()).strip()

    @property
    def has_body(self) -> bool:
        return bool(self.body and self.body.strip())

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
    """GDELT's DOC API.

    It enforces roughly one request every 5 seconds, and it says so in a
    **plain-text body that does not always carry an error status** — a 200 whose
    content is "Please limit requests to one every 5 seconds". `raise_for_status`
    sails past that and `.json()` then fails with a decode error that reads like
    malformed data rather than a rate limit. This source was returning zero
    articles for that reason while looking like a quiet news day, which is the
    exact confusion docs/DATA_SOURCES.md warns about.
    """

    name = "gdelt"

    #: Shared across instances: the limit is per client, not per object.
    _last_request_at: float = 0.0

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
        payload = self._get_with_backoff(params)
        if payload is None:
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

    def _get_with_backoff(self, params: dict) -> dict | None:
        settings = get_settings()
        interval = settings.gdelt_min_interval_s

        for attempt in range(settings.gdelt_max_retries):
            self._wait_for_slot(interval)
            try:
                r = httpx.get(
                    GDELT_URL,
                    params=params,
                    timeout=30.0,
                    headers={"User-Agent": "NAVARA/0.1 (safe-route research)"},
                )
            except httpx.HTTPError as exc:
                log.warning("GDELT request failed: %s", exc)
                return None

            limited = r.status_code == 429 or _is_rate_limit_body(r.text)
            if limited:
                # Back off further each time rather than hammering a service
                # that has just asked us not to.
                wait = interval * (attempt + 2)
                log.info(
                    "GDELT rate-limited (attempt %d/%d), waiting %.0fs",
                    attempt + 1,
                    settings.gdelt_max_retries,
                    wait,
                )
                time.sleep(wait)
                continue

            if r.status_code >= 400:
                log.warning("GDELT returned HTTP %d", r.status_code)
                return None

            try:
                return r.json()
            except ValueError:
                log.warning("GDELT returned non-JSON: %s", r.text[:160])
                return None

        log.warning(
            "GDELT still rate-limited after %d attempts — no articles from it this run",
            settings.gdelt_max_retries,
        )
        return None

    @classmethod
    def _wait_for_slot(cls, interval: float) -> None:
        elapsed = time.monotonic() - cls._last_request_at
        if cls._last_request_at and elapsed < interval:
            time.sleep(interval - elapsed)
        cls._last_request_at = time.monotonic()


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


#: GDELT's rate-limit notice, which arrives as a plain-text body rather than a
#: structured error and sometimes under a 200.
_RATE_LIMIT_MARKERS = ("limit requests", "too many requests", "rate limit")


def _is_rate_limit_body(text: str) -> bool:
    head = (text or "")[:400].lower()
    return any(marker in head for marker in _RATE_LIMIT_MARKERS)


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
