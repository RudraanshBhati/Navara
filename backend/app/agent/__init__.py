"""The NAVARA news agent.

Gathers recent Delhi news, extracts located safety incidents from it, and
writes them into the NSI layer so route scoring reflects what happened this
week — not just what the crime statistics said last year.

See `graph.py` for the pipeline. Run it with `scripts/run_news_agent.py`.
"""

from .graph import build_graph, run_agent
from .sources import Article, NewsSource, default_sources

__all__ = ["build_graph", "run_agent", "Article", "NewsSource", "default_sources"]
