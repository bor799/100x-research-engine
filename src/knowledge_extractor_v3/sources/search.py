"""Search source adapter for discovering content via web search.

Uses Exa API to find relevant articles/posts based on queries.
Supports scheduled re-searching to discover new content.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from .models import SourceConfig, SourceItem
from ..fetchers.search import SearchChannelAdapter, create_search_adapter
from ..models import TypedError
from ..queue_store import FailureKind, NextAction


class SearchAdapter:
    """Search-based source discovery adapter.

    Unlike RSS (which polls a fixed feed), Search sources run queries
    to discover content. This is useful for:
    - Topic monitoring (e.g., "AI news 2024")
    - Brand monitoring (e.g., "company:OpenAI news")
    - Trend discovery (e.g., "Hacker News best of")

    Usage:
        adapter = SearchAdapter(api_key="xxx")
        source = SourceConfig(
            id="search-ai-news",
            source_type="search",
            url="search://AI industry news 2024",
            config={
                "query": "AI industry news 2024",
                "num_results": 10,
                "recency": "oneWeek",
            },
        )
        items = adapter.discover(source, lookback_days=7)
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        use_mcp: bool = False,
    ) -> None:
        """Initialize search adapter.

        Args:
            api_key: Exa API key (optional, also reads EXA_API_KEY env var)
            use_mcp: Use mcporter + MCP server instead of direct API
        """
        self._search_adapter = create_search_adapter(api_key=api_key, use_mcp=use_mcp)

    def source_type(self) -> str:
        return "search"

    def discover(
        self,
        source: SourceConfig,
        *,
        lookback_days: int = 7,
    ) -> list[SourceItem]:
        """Discover items via web search.

        Args:
            source: Source config with search parameters in config dict
            lookback_days: Only return items within this time window

        Returns:
            List of SourceItem discovered from search results
        """
        config = source.config or {}

        # Extract search parameters
        query = config.get("query", "")
        if not query:
            # Try to extract from URL (search://query format)
            query = source.url.replace("search://", "")

        if not query:
            return []

        num_results = int(config.get("num_results", 10))
        category = config.get("category")
        domain = config.get("domain")
        recency = config.get("recency", "oneWeek" if lookback_days <= 7 else "oneMonth")

        # Map lookback_days to recency
        if lookback_days <= 1:
            recency = "oneDay"
        elif lookback_days <= 7:
            recency = "oneWeek"
        elif lookback_days <= 30:
            recency = "oneMonth"
        elif lookback_days <= 365:
            recency = "oneYear"
        else:
            recency = "noLimit"

        # Perform search
        results = self._search_adapter.search(
            query,
            num_results=num_results,
            category=category,
            domain=domain,
            recency=recency,
        )

        # Convert to SourceItem
        items = []
        for result in results:
            url = result.get("url", "")
            if not url:
                continue

            # Parse published date if available
            published_at = result.get("published_at", "")
            if published_at:
                try:
                    # Check if within lookback window
                    pub_dt = self._parse_date(published_at)
                    if pub_dt:
                        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
                        if pub_dt < cutoff:
                            continue
                except Exception:
                    pass  # Include if we can't parse date

            item = SourceItem(
                source_id=source.id,
                source_type="search",
                url=url,
                title=result.get("title", ""),
                published_at=published_at,
                author=result.get("author", ""),
                priority=source.priority,
                metadata={
                    "search_query": query,
                    "search_result": result,
                    "category": category or "",
                    "domain": domain or "",
                    "tags": source.tags,
                    **source.metadata,
                },
            )
            items.append(item)

        return items

    @staticmethod
    def _parse_date(date_str: str) -> datetime | None:
        """Parse various date formats."""
        if not date_str:
            return None

        # Try ISO format
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            pass

        # Try common formats
        from email.utils import parsedate_to_datetime
        try:
            return parsedate_to_datetime(date_str)
        except (TypeError, ValueError, IndexError):
            pass

        return None

    def health_check(self) -> dict[str, str]:
        """Check if search is available."""
        try:
            results = self._search_adapter.search("test", num_results=1)
            if results:
                return {"status": "ok", "mode": "api" if self._search_adapter._api_key else "mcp"}
            return {"status": "error", "message": "No results returned"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


def create_search_source(
    *,
    id: str,
    query: str,
    num_results: int = 10,
    recency: str = "oneWeek",
    category: str | None = None,
    domain: str | None = None,
    tags: list[str] | None = None,
    priority: int = 0,
    metadata: dict[str, Any] | None = None,
) -> SourceConfig:
    """Factory to create a search source config.

    Args:
        id: Unique source identifier
        query: Search query
        num_results: Number of results to fetch per run
        recency: Time filter (oneDay, oneWeek, oneMonth, oneYear, noLimit)
        category: Exa category filter (company, people, etc.)
        domain: Restrict to specific domain
        tags: Tags for this source
        priority: Source priority (higher = processed first)
        metadata: Additional metadata

    Returns:
        SourceConfig ready for use with SearchAdapter
    """
    return SourceConfig(
        id=id,
        source_type="search",
        url=f"search://{query}",
        config={
            "query": query,
            "num_results": num_results,
            "recency": recency,
            "category": category,
            "domain": domain,
        },
        tags=tags or [],
        priority=priority,
        metadata=metadata or {},
    )
