"""Web discovery source adapter for sites without RSS feeds.

Uses Jina Reader (r.jina.ai) to fetch a page as clean markdown,
then extracts article links. Works for JavaScript-rendered sites
like elsewhere.news that don't provide RSS/Atom feeds.

Generic: any site can be added as ``type: web_discovery``.

Optional source metadata:
  - link_pattern: regex to filter article URLs (e.g. ``/zh/\\w+/[\\w-]+``)
  - exclude_paths: list of path prefixes to skip (e.g. ``["/about", "/terms"]``)
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from .models import SourceConfig, SourceItem
from ..models import TypedError
from ..fetchers.http_client import HttpClient, create_http_client
from ..queue_store import FailureKind, NextAction


def _extract_article_links(
    content: str,
    base_url: str,
    *,
    link_pattern: str = "",
    exclude_prefixes: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Extract ``(url, title)`` pairs from page content.

    Returns absolute URLs on the same domain as *base_url*.
    """
    base_domain = urlparse(base_url).netloc.removeprefix("www.")
    exclude_prefixes = exclude_prefixes or []

    # Markdown links: [title](url)
    md_link_re = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
    # Bare URLs (fallback when no markdown links are found)
    bare_url_re = re.compile(r"(https?://[^\s\]\)<\"]+)")
    custom_re = re.compile(link_pattern) if link_pattern else None

    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    def _try_add(url: str, title: str) -> None:
        url = url.rstrip(".,;)")
        link_domain = urlparse(url).netloc.removeprefix("www.")
        if link_domain != base_domain:
            return
        if url.rstrip("/") == base_url.rstrip("/"):
            return
        path = urlparse(url).path
        for prefix in exclude_prefixes:
            if path.startswith(prefix):
                return
        if custom_re and not custom_re.search(url):
            return
        if url not in seen:
            seen.add(url)
            results.append((url, title or url))

    for match in md_link_re.finditer(content):
        _try_add(match.group(2), match.group(1).strip())

    # If markdown links found nothing, try bare URLs
    if not results:
        for match in bare_url_re.finditer(content):
            _try_add(match.group(1), "")

    return results


class WebDiscoveryAdapter:
    """Discover article URLs from a website using Jina Reader.

    Fetches the page via Jina Reader (renders JS, returns clean markdown),
    extracts article links, and returns them as :class:`SourceItem` instances.
    """

    def __init__(
        self,
        *,
        timeout: int = 30,
        http_client: HttpClient | None = None,
    ) -> None:
        self._timeout = timeout
        self._http_client = http_client or create_http_client(timeout=timeout)
        self.last_error: TypedError | None = None

    def source_type(self) -> str:
        return "web_discovery"

    def discover(
        self,
        source: SourceConfig,
        *,
        lookback_days: int = 7,
    ) -> list[SourceItem]:
        """Discover article URLs from a web page."""
        self.last_error = None

        meta = source.metadata or {}
        link_pattern = str(meta.get("link_pattern", ""))
        exclude_paths = meta.get("exclude_paths", [])
        if isinstance(exclude_paths, str):
            exclude_paths = [p.strip() for p in exclude_paths.split(",")]

        # Jina Reader renders JS and returns clean markdown — best for SPAs
        response = self._http_client.get_via_jina(source.url)

        if isinstance(response, TypedError):
            self.last_error = response
            return []

        if not response.is_success:
            self.last_error = TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message=f"Web discovery fetch returned HTTP {response.status}",
                stage="source.web_discovery",
                retryable=response.status >= 500,
                next_action=(
                    NextAction.RETRY_LATER
                    if response.status >= 500
                    else NextAction.MANUAL_REVIEW
                ),
                detail=source.url,
            )
            return []

        links = _extract_article_links(
            response.content,
            source.url,
            link_pattern=link_pattern,
            exclude_prefixes=exclude_paths,
        )

        if not links:
            self.last_error = TypedError(
                failure_kind=FailureKind.PARSE_ERROR,
                message="Web discovery found no article links",
                stage="source.web_discovery",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=source.url,
            )
            return []

        extra_meta = {
            k: v
            for k, v in meta.items()
            if k not in {"link_pattern", "exclude_paths"}
        }

        items: list[SourceItem] = []
        for url, title in links:
            item = SourceItem(
                source_id=source.id,
                source_type="web_discovery",
                url=url,
                title=title,
                priority=source.priority,
                metadata={
                    "discovered_from": source.url,
                    "via_jina": response.via_jina,
                    "tags": source.tags,
                    **extra_meta,
                },
            )
            items.append(item)

        return items
