"""Article body fetching, and the extraction guards that depend on it.

The agent used to extract from headlines: GDELT supplies no summary at all and
roughly half of Delhi RSS items carry one, which measured out at a median 115
characters of input. Asking a model to name the street an incident happened on,
given a sentence that contains no street, gets you either nothing or a guess —
and a guess here puts a warning on a road where nothing happened.

These tests cover the two halves of the fix: that bodies get fetched and used,
and that a location too coarse to place on a 500 m grid never survives.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.agent.extract import COARSE_LOCATIONS, is_too_coarse
from app.agent.fetch import ArticleFetcher, extract_text
from app.agent.sources import Article, _is_rate_limit_body

ARTICLE_HTML = """
<html><body>
  <nav><a href="/">Home</a><a href="/delhi">Karol Bagh news</a></nav>
  <article>
    <h1>Woman robbed near Saket Metro station</h1>
    <p>A 27-year-old woman was robbed at knifepoint late on Tuesday night while
    walking from Saket Metro station towards Pushp Vihar, police said. An FIR
    has been registered at the Malviya Nagar police station and the police are
    reviewing CCTV footage from the area to identify the two accused.</p>
  </article>
  <aside class="related">Related: Chain snatching reported in Lajpat Nagar</aside>
</body></html>
"""


def _article(url: str = "https://news.test/a1", **kw) -> Article:
    base = {
        "title": "Woman robbed near Saket Metro station",
        "url": url,
        "source": "fixture",
        "published_at": datetime.now(UTC),
    }
    return Article(**{**base, **kw})


@pytest.fixture
def fetcher(tmp_path, monkeypatch):
    """A fetcher with no throttle and a temp cache."""
    import app.agent.fetch as fetch_mod

    monkeypatch.setattr(fetch_mod, "CACHE_PATH", tmp_path / "article_cache.json")
    return ArticleFetcher(delay_s=0.0)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def test_extraction_gets_the_article_and_drops_the_furniture():
    """Boilerplate is not noise here — it is a wrong answer that looks right.

    A "related stories" sidebar naming Lajpat Nagar would hand the extractor a
    place from a different incident, and it geocodes perfectly.
    """
    text = extract_text(ARTICLE_HTML)

    assert "Saket Metro station" in text
    assert "Pushp Vihar" in text
    assert "Lajpat Nagar" not in text  # the related-stories sidebar
    assert "Home" not in text  # the nav


def test_extraction_survives_junk_input():
    assert extract_text("") == ""
    assert extract_text("<html></html>") == ""


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------


@respx.mock
def test_a_fetched_body_reaches_the_article_text(fetcher):
    respx.get("https://news.test/a1").mock(return_value=httpx.Response(200, text=ARTICLE_HTML))
    article = _article()

    article.body = fetcher.body(article.url)

    assert article.has_body
    assert "Pushp Vihar" in article.text
    assert article.title in article.text  # title survives, first


@respx.mock
def test_a_paywall_is_not_an_error(fetcher):
    """The article still goes to the extractor on what we already had."""
    respx.get("https://news.test/a1").mock(return_value=httpx.Response(403))
    article = _article()

    article.body = fetcher.body(article.url)

    assert article.body == ""
    assert not article.has_body
    assert article.title in article.text


@respx.mock
def test_a_consent_wall_is_rejected_as_too_short(fetcher):
    """Otherwise the model hunts for a crime location in a cookie notice."""
    stub = "<html><body><article><p>Please accept cookies to continue.</p></article></body></html>"
    respx.get("https://news.test/a1").mock(return_value=httpx.Response(200, text=stub))

    assert fetcher.body("https://news.test/a1") == ""
    assert fetcher.stats.too_short == 1


@respx.mock
def test_a_url_is_fetched_once_ever(fetcher):
    route = respx.get("https://news.test/a1").mock(
        return_value=httpx.Response(200, text=ARTICLE_HTML)
    )

    first = fetcher.body("https://news.test/a1")
    second = fetcher.body("https://news.test/a1")

    assert first == second
    assert route.call_count == 1


@respx.mock
def test_failures_are_cached_too(fetcher):
    """A paywalled URL keeps reappearing in the feeds. Learn it once."""
    route = respx.get("https://news.test/a1").mock(return_value=httpx.Response(403))

    fetcher.body("https://news.test/a1")
    fetcher.body("https://news.test/a1")

    assert route.call_count == 1


@respx.mock
def test_the_cache_survives_a_round_trip(tmp_path, monkeypatch):
    import app.agent.fetch as fetch_mod

    cache = tmp_path / "article_cache.json"
    monkeypatch.setattr(fetch_mod, "CACHE_PATH", cache)
    respx.get("https://news.test/a1").mock(return_value=httpx.Response(200, text=ARTICLE_HTML))

    first = ArticleFetcher(delay_s=0.0)
    first.body("https://news.test/a1")
    first.save_cache()

    assert json.loads(cache.read_text(encoding="utf-8"))
    assert ArticleFetcher(delay_s=0.0).body("https://news.test/a1")


def test_an_empty_url_is_not_fetched(fetcher):
    assert fetcher.body("") == ""
    assert fetcher.stats.attempted == 0


# ---------------------------------------------------------------------------
# Coarse locations
# ---------------------------------------------------------------------------


def test_a_district_is_not_a_location():
    """Spreading a penalty over a district looks like information and is not.

    "South Delhi" geocodes cleanly to a centroid, so nothing downstream would
    flag it — the incident simply lands on whichever cells surround a point
    where nothing happened.
    """
    for coarse in ("Delhi", "South Delhi", "NCR", "New Delhi, India", "  west delhi  "):
        assert is_too_coarse(coarse), coarse


def test_a_real_place_survives():
    for fine in (
        "Saket Metro Station, New Delhi",
        "Karol Bagh market, Delhi",
        "Ajmal Khan Road, Karol Bagh",
        "Lajpat Nagar Central Market",
    ):
        assert not is_too_coarse(fine), fine


def test_an_empty_location_is_coarse():
    assert is_too_coarse("")
    assert is_too_coarse("   ")


def test_a_district_with_a_suffix_is_still_a_district():
    """'South Delhi, Delhi' is the same non-answer wearing a qualifier."""
    assert is_too_coarse("South Delhi, Delhi")
    assert is_too_coarse("New Delhi, Delhi, India")


def test_every_coarse_entry_is_normalised():
    """A stray capital or space in the constant would silently disable an entry."""
    for name in COARSE_LOCATIONS:
        assert name == " ".join(name.lower().split())


# ---------------------------------------------------------------------------
# GDELT rate limiting
# ---------------------------------------------------------------------------


def test_the_rate_limit_notice_is_recognised():
    """GDELT sends this as a plain body, sometimes under HTTP 200.

    raise_for_status sails past it and .json() then fails with a decode error
    that reads like malformed data rather than "you are going too fast".
    """
    body = (
        "Please limit requests to one every 5 seconds or contact "
        "kalev.leetaru5@gmail.com for larger queries."
    )
    assert _is_rate_limit_body(body)


def test_real_json_is_not_mistaken_for_a_rate_limit():
    assert not _is_rate_limit_body('{"articles": [{"title": "Robbery in Karol Bagh"}]}')
    assert not _is_rate_limit_body("")
