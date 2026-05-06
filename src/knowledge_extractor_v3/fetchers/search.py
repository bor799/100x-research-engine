"""Search channel adapter using Exa API.

Provides web search capability via Exa (https://exa.ai).
Supports both direct API calls and MCP server integration.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode

from ..models import FetchedContent, TypedError, sha256_text, utc_now
from ..queue_store import FailureKind, NextAction
from .http_client import HttpClient, create_http_client


class ExaSearchResult:
    """Parsed Exa search result."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.title = str(data.get("title", ""))
        self.url = str(data.get("url", ""))
        self.content = str(data.get("text", "")).strip()
        self.published_date = str(data.get("publishedDate", ""))
        self.author = str(data.get("author", ""))
        self.score = float(data.get("score", 0.0))
        self.raw = data

    def is_valid(self) -> bool:
        return bool(self.url and (self.title or self.content))


class SearchChannelAdapter:
    """Search adapter using Exa API.

    Supports two modes:
    1. Direct API: uses EXA_API_KEY env var
    2. MCP server: uses mcporter to call exa MCP server

    Usage:
        adapter = SearchChannelAdapter(api_key="xxx")
        results = adapter.search("AI news 2024", num_results=5)
    """

    EXA_API_BASE = "https://api.exa.ai"
    EXA_SEARCH_ENDPOINT = "/search"
    MCP_SERVER = "exa"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        mcporter_path: str | None = None,
        use_mcp: bool = False,
        http_client: HttpClient | None = None,
        timeout: int = 30,
    ) -> None:
        """Initialize search adapter.

        Args:
            api_key: Exa API key (defaults to EXA_API_KEY env var)
            mcporter_path: Path to mcporter binary (for MCP mode)
            use_mcp: Force MCP mode even if API key is available
            http_client: HTTP client for direct API calls
            timeout: Request timeout in seconds
        """
        self._api_key = api_key or os.environ.get("EXA_API_KEY")
        self._mcporter_path = mcporter_path
        self._use_mcp = use_mcp
        self._http_client = http_client or create_http_client(timeout=timeout)
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "search"

    def can_handle(self, url: str) -> bool:
        """Search adapter doesn't handle direct URLs."""
        return False

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
            query: Search query (natural language recommended)
            num_results: Number of results (1-100)
            category: Filter by category (company, people, etc.)
            domain: Restrict to specific domain
            recency: Time filter (oneDay, oneWeek, oneMonth, oneYear, noLimit)

        Returns:
            List of search results with keys: title, url, content, published_at, author
        """
        if self._use_mcp or not self._api_key:
            return self._search_via_mcp(query, num_results=num_results, category=category)
        return self._search_via_api(query, num_results=num_results, domain=domain, recency=recency)

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Not applicable for search adapter."""
        return None

    def check(self, config: dict[str, Any]) -> str:
        """Check if search is available."""
        if self._api_key:
            try:
                results = self._search_via_api("test", num_results=1)
                return "ok" if results else "api_error"
            except Exception:
                return "api_error"
        if self._mcporter_path or shutil.which("mcporter"):
            return "mcp_available"
        return "no_credentials"

    def _search_via_api(
        self,
        query: str,
        *,
        num_results: int = 10,
        domain: str | None = None,
        recency: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search using Exa REST API directly."""
        if not self._api_key:
            return []

        url = f"{self.EXA_API_BASE}{self.EXA_SEARCH_ENDPOINT}"
        headers = {
            "Content-Type": "application/json",
            "x-api-key": self._api_key,
        }

        payload: dict[str, Any] = {
            "query": query,
            "numResults": num_results,
            "contents": {
                "text": True,
            },
        }

        # Add optional filters
        if category:
            payload["category"] = category
        if domain:
            payload["domain"] = domain
        if recency:
            payload["recency"] = recency

        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
                return self._parse_exa_response(result)
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, OSError):
            return []

    def _search_via_mcp(
        self,
        query: str,
        *,
        num_results: int = 10,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search using mcporter and Exa MCP server."""
        mcporter = self._mcporter_path or shutil.which("mcporter")
        if not mcporter:
            return []

        # Build mcporter call
        mcp_query = f'web_search_exa(query: "{query}", numResults: {num_results})'
        if category:
            mcp_query = f'web_search_exa(query: "category:{category} {query}", numResults: {num_results})'

        try:
            cmd = [str(mcporter), "call", mcp_query]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=self._timeout + 10, check=False)
            if result.returncode != 0:
                return []

            # Parse mcporter JSON output
            data = json.loads(result.stdout)
            results = data.get("results", [])

            parsed = []
            for item in results:
                parsed.append({
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "content": str(item.get("summary", item.get("text", ""))).strip(),
                    "published_at": item.get("publishedDate", item.get("date", "")),
                    "author": item.get("author", ""),
                    "source": "exa-mcp",
                })
            return parsed
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            return []

    def _parse_exa_response(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse Exa API response into standardized format."""
        results = data.get("results", [])
        parsed = []

        for item in results:
            result = ExaSearchResult(item)
            if result.is_valid():
                parsed.append({
                    "title": result.title,
                    "url": result.url,
                    "content": result.content,
                    "published_at": result.published_date,
                    "author": result.author,
                    "source": "exa-api",
                })

        return parsed

    def fetch_to_content(
        self,
        search_result: dict[str, Any],
        query: str = "",
    ) -> FetchedContent | TypedError:
        """Convert a search result to FetchedContent.

        Args:
            search_result: Parsed search result dict
            query: Original search query (for metadata)

        Returns:
            FetchedContent for use in V3 pipeline
        """
        url = search_result.get("url", "")
        content = search_result.get("content", "")

        if not url:
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message="Search result missing URL",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=str(search_result),
            )

        return FetchedContent(
            url=url,
            source=search_result.get("source", "exa-search"),
            source_type="search_result",
            title=search_result.get("title", "")[:200],
            text=content,
            raw=content,
            author=search_result.get("author", ""),
            published_at=search_result.get("published_at", ""),
            fetched_at=utc_now(),
            content_hash=sha256_text(content),
            metadata={
                "search_query": query,
                "search_source": search_result.get("source", "exa"),
            },
        )


import shutil


def create_search_adapter(
    *,
    api_key: str | None = None,
    use_mcp: bool = False,
) -> SearchChannelAdapter:
    """Factory to create search adapter with auto-detection."""
    # Auto-detect mcporter
    mcporter_path = shutil.which("mcporter")
    return SearchChannelAdapter(api_key=api_key, mcporter_path=mcporter_path, use_mcp=use_mcp)
