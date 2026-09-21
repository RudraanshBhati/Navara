"""Turning a headline into a structured, scoreable incident.

This is the judgement step of the agent. A Delhi city-desk feed is mostly not
about pedestrian safety — it is traffic, politics, weather, and court reporting
— and the incidents that *are* relevant bury their location in prose ("near the
Saket metro station", "in a lane off Karol Bagh main road").

We ask the model for a strict schema and, importantly, for a `confidence` and a
`location_text`. Those two fields carry most of the weight downstream:

  - `confidence` scales the incident's contribution to the NSI score, so a
    hedged extraction moves the map less than a clear one.
  - `location_text` is what goes to the geocoder. If the model cannot name a
    place more specific than "Delhi", the incident is dropped rather than
    smeared across the whole city — a city-wide penalty is the same as no
    penalty, but it looks like information.
"""

from __future__ import annotations

import logging
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from ..config import get_settings
from ..data.base import SEVERITY_WEIGHTS
from .sources import Article

log = logging.getLogger(__name__)

CATEGORIES = list(SEVERITY_WEIGHTS.keys())

#: Place names too coarse to put on a 500 m grid. The prompt asks the model to
#: avoid these; this enforces it, because a prompt is a request and this is the
#: field that decides which streets get a warning on them.
#:
#: Geocoding "South Delhi" succeeds — it returns a district centroid with a
#: confident-looking result — and the incident then lands on whichever cells
#: happen to surround that point, which is not where anything happened. A miss
#: is visible and costs one data point; this is invisible and wrong.
COARSE_LOCATIONS = {
    "delhi",
    "new delhi",
    "ncr",
    "delhi ncr",
    "national capital region",
    "north delhi",
    "south delhi",
    "east delhi",
    "west delhi",
    "central delhi",
    "outer delhi",
    "north east delhi",
    "north west delhi",
    "south east delhi",
    "south west delhi",
    "north-east delhi",
    "north-west delhi",
    "south-east delhi",
    "south-west delhi",
    "shahdara",
    "delhi police",
    "india",
}


def is_too_coarse(location_text: str) -> bool:
    """True when a location names an area rather than a place."""
    cleaned = " ".join((location_text or "").lower().split())
    cleaned = cleaned.strip(" ,.")
    if not cleaned:
        return True
    if cleaned in COARSE_LOCATIONS:
        return True
    # "South Delhi, Delhi" and "New Delhi, India" are the same non-answer with a
    # suffix attached. Strip trailing region qualifiers and re-check.
    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    meaningful = [p for p in parts if p not in COARSE_LOCATIONS]
    return not meaningful


class ExtractedIncident(BaseModel):
    """Schema the model must fill in for each article."""

    is_safety_incident: bool = Field(
        description=(
            "True only if this article reports a specific criminal or safety "
            "incident that happened at an identifiable location in Delhi NCT, "
            "and that would matter to someone deciding whether to walk there. "
            "False for policy news, court proceedings about old cases, traffic "
            "accidents, national news, and crime statistics reporting."
        )
    )
    # NOTE ON REQUIRED FIELDS
    # Every field that moves the score is required and carries no default.
    # A field with a default is one a model may simply not answer, and smaller
    # models routinely don't: measured across local models, `category` came back
    # "other" on 100% of extractions and one model returned confidence 0.50 on
    # every article — both of which were the schema defaults showing through, not
    # judgements. Since severity_of("other") is 0.4, that silently flattened a
    # sexual assault (1.0) and a snatching (0.6) to the same weight, defeating
    # the severity weighting RESEARCH.md calls not optional.
    category: Literal[tuple(CATEGORIES)] = Field(  # type: ignore[valid-type]
        description=(
            "The offence type. This sets how heavily the incident is weighted, "
            "so choose the closest match rather than defaulting: sexual_assault, "
            "assault, kidnapping, murder, harassment, stalking, robbery, "
            "snatching, theft, burglary, vehicle_theft. Use 'other' only when "
            "the article genuinely describes none of these — not merely when "
            "more than one could fit, in which case pick the most severe one "
            "the article supports."
        ),
    )
    location_text: str = Field(
        description=(
            "The most specific place named in the article, as a geocodable "
            "string, e.g. 'Saket Metro Station, New Delhi' or 'Karol Bagh "
            "market, Delhi'. Return an empty string if the article names "
            "nothing more specific than a district — 'Delhi', 'South Delhi', "
            "'NCR' and the like are all too coarse to place on a 500 m grid. "
            "Answer this field explicitly either way."
        ),
    )
    occurred_at: str = Field(
        default="",
        description=(
            "ISO-8601 date (YYYY-MM-DD) the incident occurred, if the article "
            "states or implies it. Empty string if it only gives the "
            "publication date. Do not guess."
        ),
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are that this is a real, recent, correctly "
            "located Delhi safety incident. This multiplies how far the "
            "incident moves the map, so spread your answers across the range "
            "rather than anchoring on one value: 0.9+ only when the article "
            "names both a specific place and a specific event, 0.5 or below "
            "when the place is vague, the date is unclear, or you are inferring."
        ),
    )
    night_time: bool = Field(
        description="True if the article indicates the incident happened after dark.",
    )
    note: str = Field(
        default="",
        description="One short clause on what happened. Used for auditing, not display.",
    )


SYSTEM_PROMPT = """You extract pedestrian-safety incidents from Delhi news articles \
for a routing system that recommends safer walking routes.

The system uses your output to penalise specific 500-metre map cells. A wrong \
location makes the map wrong for real people walking at night, so precision \
matters more than recall:

- If the article does not name a place you could point to on a map, leave \
location_text empty. Do not infer a location from the newspaper's city desk.
- A location must be specific enough to mean one place. A named street, \
market, metro station, colony or block is specific enough. A district or a \
whole zone — "South Delhi", "West Delhi", "Outer Delhi", "NCR" — is not: \
spreading a penalty across a district is the same as no penalty, but it looks \
like information. Leave location_text empty rather than naming one.
- If you are not sure the event is recent, lower your confidence rather than \
guessing a date.
- Articles about crime statistics, policy, arrests in old cases, or court \
hearings are NOT incidents. Only report something that happened at a place.

ROAD COLLISIONS ARE NOT INCIDENTS HERE
A vehicle hitting people is a traffic accident, however many were hurt and \
however much they were on foot at the time. Set is_safety_incident false for \
it. This layer feeds a model of *crime* risk whose severity weights are \
calibrated to deliberate harm, so a collision entering it would be scored as \
though someone had been attacked. Rash driving, hit-and-run, a truck hitting \
workers or a crane, a bus mounting a pavement — all false. The exception is \
narrow: a vehicle used deliberately against a person, which the article will \
describe as an attack rather than an accident.

WHEN YOU ONLY HAVE A HEADLINE
The prompt tells you whether an article body was available. When it was not, \
you are reading a headline that was written to be short, and it will usually \
name no specific place at all. That is expected. Report what the headline \
actually supports and leave location_text empty — do not reach for the most \
plausible Delhi neighbourhood. An empty location drops the incident, which \
costs us one data point; an invented one puts a warning on a street where \
nothing happened.

Answer only with the structured fields."""

USER_PROMPT = """Article source: {source}
Published: {published}
Body available: {has_body}

Title: {title}

{content}"""


def build_llm(model: str | None = None, provider: str | None = None):
    """The extraction model, hosted or local.

    Both are asked for the same Pydantic schema, so everything downstream is
    identical — `with_structured_output` constrains an Ollama model through
    its JSON-schema mode the same way it constrains Claude through tool use.
    What differs is quality, and it differs most on the two fields that carry
    the most weight: `location_text`, which decides which streets get a warning,
    and `confidence`, which a smaller model reports far less honestly about
    itself. Weigh that against not accumulating any archive at all.
    """
    settings = get_settings()
    provider = (provider or settings.extraction_provider).strip().lower()

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model or settings.ollama_model,
            base_url=settings.ollama_base_url,
            temperature=0,
            # Deterministic-ish extraction: we want the same article to produce
            # the same incident on a re-run, so re-extraction is idempotent
            # against the archive rather than churning it.
            num_predict=settings.ollama_num_predict,
            # Both of these are load-bearing, and both fail silently when wrong
            # — the model returns an empty string rather than an error, and the
            # article is dropped with a parse warning that looks like a model
            # quality problem. See config.py for the measurements.
            num_ctx=settings.ollama_num_ctx,
            reasoning=settings.ollama_reasoning,
        )

    if provider != "anthropic":
        raise RuntimeError(
            f"Unknown EXTRACTION_PROVIDER {provider!r}. Use 'anthropic' or 'ollama'."
        )

    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to backend/.env — see .env.example.\n"
            "To run without a key, set EXTRACTION_PROVIDER=ollama and start Ollama."
        )

    return ChatAnthropic(
        model=model or settings.news_model,
        api_key=settings.anthropic_api_key,
        temperature=0,
        max_tokens=1024,
        timeout=60,
        max_retries=2,
    )


def build_chain(model: str | None = None, provider: str | None = None):
    """Prompt -> model -> validated schema."""
    llm = build_llm(model, provider)
    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", USER_PROMPT)])
    return prompt | llm.with_structured_output(ExtractedIncident)


def verify_structured_output(model: str | None = None, provider: str | None = None) -> str | None:
    """Check the model actually fills in the schema. None means it works.

    The failure this guards against is silent and expensive. A model that
    ignores the JSON schema returns confident prose; every article then fails to
    parse, the run logs a per-article warning, and the summary reports zero
    incidents — which looks exactly like a quiet news day. Four of the eight
    local models measured behaved this way. Discovering it after a week of empty
    nightly runs costs a week of archive that cannot be backfilled, because the
    articles have rotated off the feeds by then.
    """
    probe = (
        "A woman was robbed at knifepoint near Saket Metro station in Delhi on "
        "Tuesday night. An FIR was registered at Malviya Nagar police station."
    )
    name = model or (
        get_settings().ollama_model
        if (provider or get_settings().extraction_provider) == "ollama"
        else get_settings().news_model
    )

    try:
        result = build_chain(model, provider).invoke(
            {
                "source": "preflight",
                "published": "2026-01-01T00:00:00+00:00",
                "has_body": "yes",
                "title": "Woman robbed near Saket Metro station",
                "content": f"Article text:\n{probe}",
            }
        )
    except Exception as exc:  # noqa: BLE001 - the job here is to report, not raise
        return (
            f"{name} did not return the expected schema ({type(exc).__name__}). "
            "Some models ignore a JSON schema and answer in prose, which would "
            "drop every article. Try gemma3:12b, or run "
            "scripts/eval_extractor.py to compare."
        )

    if not result.is_safety_incident or is_too_coarse(result.location_text):
        return (
            f"{name} parsed the schema but found no located incident in an "
            f"unambiguous test article (got location {result.location_text!r}). "
            "It will produce empty runs. Compare models with "
            "scripts/eval_extractor.py."
        )
    return None


def extract_batch(
    articles: list[Article],
    *,
    model: str | None = None,
    provider: str | None = None,
    max_concurrency: int | None = None,
) -> list[tuple[Article, ExtractedIncident]]:
    """Extract from many articles at once.

    One article per call rather than many per call: batching articles into a
    single prompt saves tokens but makes the model far more likely to blur
    details between adjacent stories, and a location attached to the wrong
    incident is the exact failure this system cannot afford. LangChain's
    `.batch` gives us the concurrency without the blurring.
    """
    if not articles:
        return []

    settings = get_settings()
    provider = (provider or settings.extraction_provider).strip().lower()
    chain = build_chain(model, provider)

    if max_concurrency is None:
        max_concurrency = settings.ollama_max_concurrency if provider == "ollama" else 5
    inputs = [
        {
            "source": a.source,
            "published": a.published_at.isoformat(),
            "title": a.title,
            # Headline-only input is the normal case when a body could not be
            # fetched, and the model needs to know which it is looking at. Told
            # that a body exists, "no location named" is real evidence; told
            # nothing, it is indistinguishable from "the body was not provided",
            # and a model asked to find a street in a headline will invent one.
            "has_body": "yes" if a.has_body else "no — headline and summary only",
            "content": _content(a, settings.article_max_chars),
        }
        for a in articles
    ]

    results = chain.batch(
        inputs,
        config={"max_concurrency": max_concurrency},
        return_exceptions=True,
    )

    paired: list[tuple[Article, ExtractedIncident]] = []
    failures = 0
    for article, result in zip(articles, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("extraction failed for %s: %s", article.url, result)
            failures += 1
            continue
        if provider == "ollama":
            result.confidence = min(result.confidence, settings.ollama_confidence_ceiling)
        paired.append((article, result))

    # Every article failing is a configuration problem, not a news problem, and
    # the per-article warnings above scroll past. Say it once, plainly.
    if failures and not paired:
        log.error(
            "Every article failed to parse. %s is probably not honouring the "
            "output schema — check with scripts/eval_extractor.py.",
            model or (settings.ollama_model if provider == "ollama" else settings.news_model),
        )

    log.info(
        "extracted %d/%d articles via %s",
        len(paired),
        len(articles),
        provider,
    )
    return paired


def _content(article: Article, max_chars: int) -> str:
    """The reading material, labelled so the model knows what it has."""
    if article.has_body:
        return f"Article text:\n{article.body[:max_chars]}"
    if article.summary.strip():
        return f"Summary (no article body available):\n{article.summary[:1500]}"
    return "No summary or article body could be retrieved — you have only the title."
