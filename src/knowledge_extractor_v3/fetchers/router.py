"""Fetcher router for V3 worker tasks.

Supports:
- URL fetching through Agent Reach multi-channel adapters
- Web search via Exa API/MCP
"""

from __future__ import annotations

from typing import Any

from ..models import FetchedContent, TypedError
from .base import Fetcher
from .fixture import FixtureFetcher
from .multi_channel import AgentReachFetcher
from .search import SearchChannelAdapter, create_search_adapter


class FetcherRouter:
    """Route each URL to the safest available V3 fetcher.

    Also supports web search via Exa API.
    """

    def __init__(
        self,
        *,
        fixture_fetcher: Fetcher | None = None,
        web_fetcher: Fetcher | None = None,
        agent_reach_fetcher: Fetcher | None = None,
        search_adapter: SearchChannelAdapter | None = None,
    ) -> None:
        self.fixture_fetcher = fixture_fetcher or FixtureFetcher()
        self.web_fetcher = web_fetcher
        self.agent_reach_fetcher = agent_reach_fetcher or web_fetcher or AgentReachFetcher()
        self.search_adapter = search_adapter or create_search_adapter()

    def fetch(self, url: str) -> FetchedContent | TypedError:
        """Fetch content from a URL."""
        if url.startswith("fixture://"):
            return self.fixture_fetcher.fetch(url)
        return self.agent_reach_fetcher.fetch(url)

    def search(
        self,
        query: str,
        *,
        num_results: int = 10,
        category: str | None = None,
        domain: str | None = None,
        recency: str | None = None,
    ) -> list[dict[str, Any]]:
        """Perform web search using Exa.

        Args:
            query: Search query (natural language)
            num_results: Number of results (1-100)
            category: Filter by category (company, people, etc.)
            domain: Restrict to specific domain
            recency: Time filter (oneDay, oneWeek, oneMonth, oneYear, noLimit)

        Returns:
            List of search results with keys: title, url, content, published_at, author
        """
        return self.search_adapter.search(
            query,
            num_results=num_results,
            category=category,
            domain=domain,
            recency=recency,
        )

    def search_and_fetch(
        self,
        query: str,
        *,
        num_results: int = 5,
    ) -> list[FetchedContent | TypedError]:
        """Search and fetch full content for top results.

        Args:
            query: Search query
            num_results: Number of results to fetch

        Returns:
            List of FetchedContent (or TypedError for failed fetches)
        """
        search_results = self.search(query, num_results=num_results)

        fetched = []
        for result in search_results:
            url = result.get("url", "")
            if url:
                content = self.fetch(url)
                fetched.append(content)

        return fetched
