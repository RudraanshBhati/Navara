"""Fetching the body of a news article, so extraction has something to read.

WHY THIS EXISTS
---------------
Without it the agent extracts from headlines. GDELT returns no summary field at
all, and only about half of Delhi RSS items carry one — measured median article
text was 115 characters, which is a headline and nothing else.

That matters because of what we ask the model for. `location_text` is the most
load-bearing field in the pipeline: it decides which 500 m cell gets penalised,
and extract.py is explicit that a wrong location makes the map wrong for real
people walking at night. Headlines rarely name a place more specific than
"Delhi" — the location lives in the body ("in a lane off Karol Bagh main road",
"near the Saket metro station"). Asking a model to find a street in a sentence
that contains no street produces either an empty answer or an invented one.

WHY A REAL EXTRACTOR AND NOT A REGEX
------------------------------------
Stripping tags naively leaves navigation, ad copy and "related stories" in the
text. A related-stories sidebar mentioning Karol Bagh would hand the extractor a
place name from a different story entirely, and it would geocode cleanly and
penalise the wrong neighbourhood. Boilerplate contamination here is not noise,
it is a wrong answer that looks right. trafilatura removes it.

POLITENESS
----------
These are real newsrooms' servers and we are an uninvited client. One request at
a time, a delay between them, a short timeout, a real User-Agent, and a
persistent cache so a story is fetched once ever rather than once per run.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import PROCESSED_DIR, get_settings

log = logging.getLogger(__name__)

CACHE_PATH = PROCESSED_DIR / "article_cache.json"

USER_AGENT = "NAVARA/0.1 (safe-route research; https://github.com/RudraanshBhati/Navara)"

#: Paywalls, consent walls and bot blocks are the normal case for a slice of
#: these sites, not an exception worth failing a run over.
_SKIP_STATUS = {401, 402, 403, 404, 410, 451}


@dataclass
class FetchStats:
    attempted: int = 0
    fetched: int = 0
    cached: int = 0
    failed: int = 0
    too_short: int = 0

    def as_dict(self) -> dict:
        return {
            "bodies_attempted": self.attempted,
            "bodies_fetched": self.fetched,
            "bodies_cached": self.cached,
            "bodies_failed": self.failed,
            "bodies_too_short": self.too_short,
        }


class ArticleFetcher:
    """Fetches and caches article bodies.

    The cache stores failures as well as successes. A paywalled URL will keep
    reappearing in the feeds, and retrying it every run costs time and goodwill
    to learn the same thing.
    """

    def __init__(self, delay_s: float | None = None) -> None:
        settings = get_settings()
        self.delay_s = settings.article_fetch_delay_s if delay_s is None else delay_s
        self.timeout_s = settings.article_fetch_timeout_s
        self.max_chars = settings.article_max_chars
        self.min_chars = settings.article_min_chars
        self.stats = FetchStats()
        self._cache: dict[str, str | None] = {}
        self._last_request = 0.0
        self._load_cache()

    # -- cache ------------------------------------------------------------

    def _load_cache(self) -> None:
        if CACHE_PATH.exists():
            try:
                self._cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("article cache corrupt, starting fresh")
                self._cache = {}

    def save_cache(self) -> None:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(self._cache), encoding="utf-8")

    # -- fetching ---------------------------------------------------------

    def body(self, url: str) -> str:
        """Article text for a URL, or "" if it could not be read."""
        if not url:
            return ""

        if url in self._cache:
            self.stats.cached += 1
            return self._cache[url] or ""

        self.stats.attempted += 1
        text = self._fetch(url)
        self._cache[url] = text
        return text or ""

    def _fetch(self, url: str) -> str | None:
        self._throttle()
        try:
            r = httpx.get(
                url,
                timeout=self.timeout_s,
                follow_redirects=True,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                },
            )
        except httpx.HTTPError as exc:
            log.debug("article fetch failed %s: %s", url, exc)
            self.stats.failed += 1
            return None

        if r.status_code in _SKIP_STATUS:
            log.debug("article not readable %s: HTTP %d", url, r.status_code)
            self.stats.failed += 1
            return None
        if r.status_code >= 400:
            self.stats.failed += 1
            return None

        text = extract_text(r.text)
        if not text:
            self.stats.failed += 1
            return None

        # A very short body is usually a consent wall or a "subscribe to read"
        # stub. Treating that as the article would feed the extractor a page
        # about cookies and let it hunt for a crime location in it.
        if len(text) < self.min_chars:
            self.stats.too_short += 1
            return None

        self.stats.fetched += 1
        return text[: self.max_chars]

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_s:
            time.sleep(self.delay_s - elapsed)
        self._last_request = time.monotonic()


def extract_text(html: str) -> str:
    """Main article text out of a page, boilerplate removed."""
    try:
        import trafilatura
    except ImportError:
        log.warning("trafilatura is not installed; article bodies unavailable")
        return ""

    try:
        text = trafilatura.extract(
            html,
            include_comments=False,  # reader comments name unrelated places
            include_tables=False,
            no_fallback=False,
        )
    except Exception as exc:  # noqa: BLE001 - never let one odd page kill a run
        log.debug("trafilatura failed: %s", exc)
        return ""

    return (text or "").strip()


def _cache_path() -> Path:
    return CACHE_PATH
