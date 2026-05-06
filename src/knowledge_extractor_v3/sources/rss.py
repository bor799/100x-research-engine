"""RSS source adapter for discovering feed entries.

Supports RFC822 and ISO 8601 date formats.
Enforces lookback window and deduplication.

Uses HttpClient with User-Agent and Jina Reader fallback for 403 errors.
"""

from __future__ import annotations

from email.utils import parsedate_to_datetime
from datetime import UTC, datetime, timedelta
from typing import Any

from .models import SourceConfig, SourceItem
from ..models import TypedError
from ..fetchers.http_client import HttpClient, create_http_client
from ..queue_store import FailureKind, NextAction


# Minimal RSS/Atom parser (stdlib only)
def _parse_rss_feed(content: str, feed_url: str) -> list[dict[str, Any]]:
    """Parse RSS/Atom feed content.

    Returns list of entries with: title, link, published_at, author.
    """
    entries = []

    try:
        import xml.etree.ElementTree as ET
    except ImportError:
        return entries

    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return entries

    # Handle both RSS and Atom
    # RSS: items are under //item
    # Atom: entries are under //entry
    # Namespaces may vary

    # Try RSS first
    for item in root.findall(".//item"):
        entry = _extract_rss_item(item)
        if entry and entry.get("link"):
            entries.append(entry)

    # Try Atom if no RSS items found
    if not entries:
        for item in root.findall(".//{http://www.w3.org/2005/Atom}entry"):
            entry = _extract_atom_item(item)
            if entry and entry.get("link"):
                entries.append(entry)

    # Try Atom without namespace
    if not entries:
        for item in root.findall(".//entry"):
            entry = _extract_atom_item(item)
            if entry and entry.get("link"):
                entries.append(entry)

    return entries


def _child_text(item, *names: str) -> str:
    """Return text from the first child matching a local or namespaced tag."""
    wanted = set(names)
    for child in list(item):
        local = child.tag.rsplit("}", 1)[-1] if isinstance(child.tag, str) else child.tag
        if local in wanted and child.text:
            return child.text.strip()
    return ""


def _extract_rss_item(item) -> dict[str, Any] | None:
    """Extract entry data from RSS item element."""
    link_elem = item.find("link")
    title = _child_text(item, "title")

    link = ""
    if link_elem is not None:
        if link_elem.text:
            link = link_elem.text
        elif hasattr(link_elem, "get"):
            link = link_elem.get("href", "")

    if not link:
        return None

    published_at = _child_text(item, "pubDate", "date", "published", "updated")
    author = _child_text(item, "author", "creator")

    return {
        "title": title or "",
        "link": link,
        "published_at": published_at,
        "author": author,
    }


def _extract_atom_item(item) -> dict[str, Any] | None:
    """Extract entry data from Atom entry element."""
    title_elem = item.find("{http://www.w3.org/2005/Atom}title")
    if title_elem is None:
        title_elem = item.find("title")

    link_elem = item.find("{http://www.w3.org/2005/Atom}link")
    if link_elem is None:
        link_elem = item.find("link")

    published_elem = item.find("{http://www.w3.org/2005/Atom}published")
    if published_elem is None:
        published_elem = item.find("published")
    if published_elem is None:
        updated_elem = item.find("{http://www.w3.org/2005/Atom}updated")
        if updated_elem is None:
            updated_elem = item.find("updated")
        if updated_elem is not None:
            published_elem = updated_elem

    author_elem = item.find("{http://www.w3.org/2005/Atom}author")
    if author_elem is None:
        author_elem = item.find("author")

    title = title_elem.text if title_elem is not None and title_elem.text else ""

    link = ""
    if link_elem is not None:
        link = link_elem.get("href", "")

    if not link:
        return None

    published_at = ""
    if published_elem is not None and published_elem.text:
        published_at = published_elem.text

    author = ""
    if author_elem is not None:
        name_elem = author_elem.find("{http://www.w3.org/2005/Atom}name")
        if name_elem is None:
            name_elem = author_elem.find("name")
        if name_elem is not None and name_elem.text:
            author = name_elem.text

    return {
        "title": title,
        "link": link,
        "published_at": published_at,
        "author": author,
    }


def _parse_rss_date(date_str: str) -> datetime | None:
    """Parse RSS date string and return an aware UTC datetime."""
    date_str = date_str.strip()
    if not date_str:
        return None

    try:
        parsed = parsedate_to_datetime(date_str)
        return _to_aware_utc(parsed)
    except (TypeError, ValueError, IndexError):
        pass

    iso_candidate = date_str
    if iso_candidate.endswith("Z"):
        iso_candidate = iso_candidate[:-1] + "+00:00"
    try:
        return _to_aware_utc(datetime.fromisoformat(iso_candidate))
    except ValueError:
        pass

    formats = ["%a, %d %b %Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]

    for fmt in formats:
        try:
            return _to_aware_utc(datetime.strptime(date_str, fmt))
        except ValueError:
            continue

    return None


def _to_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class RSSAdapter:
    """RSS/Atom feed discovery adapter.

    Uses HttpClient with:
    - User-Agent headers (prevents 403 errors)
    - Jina Reader fallback (bypasses content blocking)
    - Retry logic
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
        return "rss"

    def discover(
        self,
        source: SourceConfig,
        *,
        lookback_days: int = 7,
    ) -> list[SourceItem]:
        """Discover items from RSS/Atom feed."""
        self.last_error = None

        # Fetch feed using HttpClient
        response = self._http_client.get(source.url)

        if isinstance(response, TypedError):
            self.last_error = response
            return []

        if not response.is_success:
            self.last_error = TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message=f"RSS feed returned HTTP {response.status}",
                stage="source.rss",
                retryable=response.status >= 500,
                next_action=NextAction.RETRY_LATER if response.status >= 500 else NextAction.MANUAL_REVIEW,
                detail=source.url,
            )
            return []

        feed_content = response.content

        # If Jina converted XML to markdown, try one direct raw fetch before parsing.
        if response.via_jina:
            direct_response = self._fetch_direct(source.url)
            if isinstance(direct_response, str):
                feed_content = direct_response

        # Parse feed
        entries = _parse_rss_feed(feed_content, source.url)
        if not entries:
            self.last_error = TypedError(
                failure_kind=FailureKind.PARSE_ERROR,
                message="RSS feed had no parseable entries",
                stage="source.rss",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=source.url,
            )
            return []

        # Filter by lookback window
        cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
        items = []

        for entry in entries:
            published = _parse_rss_date(entry.get("published_at", ""))

            # Skip items outside lookback window (if we can parse the date)
            if published and published < cutoff:
                continue

            item = SourceItem(
                source_id=source.id,
                source_type="rss",
                url=entry["link"],
                title=entry.get("title", ""),
                published_at=entry.get("published_at", ""),
                author=entry.get("author", ""),
                priority=source.priority,
                metadata={
                    "feed_url": source.url,
                    "tags": source.tags,
                    "category": source.metadata.get("category", ""),
                    **source.metadata,
                },
            )
            items.append(item)

        return items

    def _fetch_direct(self, url: str) -> str | TypedError:
        """Direct fetch attempt (used after Jina returns non-XML content)."""
        import urllib.request
        import urllib.error

        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, urllib.error.HTTPError):
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message="Direct fetch failed",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=url,
            )
