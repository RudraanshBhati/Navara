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
    category: Literal[tuple(CATEGORIES)] = Field(  # type: ignore[valid-type]
        default="other",
        description="The offence type that best matches. Use 'other' if unsure.",
    )
    location_text: str = Field(
        default="",
        description=(
            "The most specific place named in the article, as a geocodable "
            "string, e.g. 'Saket Metro Station, New Delhi' or 'Karol Bagh "
            "market, Delhi'. Empty string if the article names nothing more "
            "specific than 'Delhi'."
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
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "How confident you are that this is a real, recent, correctly "
            "located Delhi safety incident. Be strict: 0.9+ only when the "
            "article names a specific place and a specific event."
        ),
    )
    night_time: bool = Field(
        default=False,
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
- If you are not sure the event is recent, lower your confidence rather than \
guessing a date.
- Articles about crime statistics, policy, arrests in old cases, or court \
hearings are NOT incidents. Only report something that happened at a place.

Answer only with the structured fields."""

USER_PROMPT = """Article source: {source}
Published: {published}

Title: {title}

Summary: {summary}"""


def build_chain(model: str | None = None):
    """Prompt -> Claude -> validated schema."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to backend/.env — see .env.example."
        )

    llm = ChatAnthropic(
        model=model or settings.news_model,
        api_key=settings.anthropic_api_key,
        temperature=0,
        max_tokens=1024,
        timeout=60,
        max_retries=2,
    )
    prompt = ChatPromptTemplate.from_messages([("system", SYSTEM_PROMPT), ("human", USER_PROMPT)])
    return prompt | llm.with_structured_output(ExtractedIncident)


def extract_batch(
    articles: list[Article],
    *,
    model: str | None = None,
    max_concurrency: int = 5,
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

    chain = build_chain(model)
    inputs = [
        {
            "source": a.source,
            "published": a.published_at.isoformat(),
            "title": a.title,
            "summary": a.summary[:1500],
        }
        for a in articles
    ]

    results = chain.batch(
        inputs,
        config={"max_concurrency": max_concurrency},
        return_exceptions=True,
    )

    paired: list[tuple[Article, ExtractedIncident]] = []
    for article, result in zip(articles, results, strict=True):
        if isinstance(result, BaseException):
            log.warning("extraction failed for %s: %s", article.url, result)
            continue
        paired.append((article, result))

    log.info("extracted %d/%d articles", len(paired), len(articles))
    return paired
