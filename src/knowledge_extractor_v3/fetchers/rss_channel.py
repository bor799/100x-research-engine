"""RSS channel adapter for multi-channel fetcher.

Handles RSS/Atom feed URLs and extracts article content.
Delegates actual content fetching to WebChannelAdapter (Jina Reader).
"""

from __future__ import annotations

import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from abc import ABC
from typing import Any

from .http_client import HttpClient, create_http_client
from ..models import FetchedContent, TypedError, sha256_text, utc_now
from ..queue_store import FailureKind, NextAction


class RSSChannelAdapter:
    """RSS/Atom feed channel adapter.

    For feed URLs, parses the feed and extracts entry info.
    For article URLs within feeds, delegates to WebChannelAdapter.

    Usage:
        adapter = RSSChannelAdapter()
        result = adapter.fetch("https://example.com/feed.xml", config)
    """

    @property
    def name(self) -> str:
        return "rss"

    def can_handle(self, url: str) -> bool:
        """Check if URL is likely an RSS/Atom feed."""
        indicators = (
            "/feed",
            "/rss",
            "/atom",
            ".rss",
            ".xml",
            "feeds.feedburner.com",
            "feeds.bbci.co.uk",
        )
        url_lower = url.lower()
        return any(indicator in url_lower for indicator in indicators)

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        """Fetch RSS feed and extract basic info.

        Note: This returns feed metadata, not individual articles.
        For article discovery, use sources/rss.py instead.
        """
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(timeout=int(config.get("timeout", 30)))

        response = client.get(url)
        if isinstance(response, TypedError) or not response.is_success:
            return None

        # If Jina converted XML to markdown, it's not parseable as XML
        if response.via_jina:
            # Return the markdown content as-is
            return {
                "title": _extract_title_from_markdown(response.content) or url[:100],
                "content": response.content,
                "source": "agent-reach-rss",
                "metadata": {"via_jina": True, "feed_url": url},
            }

        # Parse as XML feed
        return self._parse_feed_xml(response.content, url)

    def check(self, config: dict[str, Any]) -> str:
        """Check RSS capability (always ok, uses WebChannelAdapter)."""
        return "ok"

    def _parse_feed_xml(self, content: str, feed_url: str) -> dict[str, Any] | None:
        """Parse RSS/Atom XML content."""
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return None

        # Extract feed title
        title = self._extract_feed_title(root) or feed_url[:100]

        # Build summary from recent items
        items_text = self._extract_items_summary(root, max_items=5)

        content = f"# {title}\n\n{items_text}" if items_text else f"# {title}\n\nFeed loaded successfully."

        return {
            "title": title,
            "content": content,
            "source": "agent-reach-rss",
            "metadata": {
                "via_jina": False,
                "feed_url": feed_url,
                "feed_type": self._detect_feed_type(root),
            },
        }

    def _extract_feed_title(self, root: ET.Element) -> str:
        """Extract feed title from RSS or Atom feed."""
        # Try RSS channel/title
        channel = root.find("channel")
        if channel is not None:
            title_elem = channel.find("title")
            if title_elem is not None and title_elem.text:
                return title_elem.text.strip()

        # Try Atom title
        title_elem = root.find("{http://www.w3.org/2005/Atom}title")
        if title_elem is None:
            title_elem = root.find("title")
        if title_elem is not None and title_elem.text:
            return title_elem.text.strip()

        return ""

    def _extract_items_summary(self, root: ET.Element, max_items: int = 5) -> str:
        """Extract summary of recent feed items."""
        lines = []

        # Try RSS items
        items = root.findall(".//item")[:max_items]
        if not items:
            # Try Atom entries
            items = root.findall(".//{http://www.w3.org/2005/Atom}entry")[:max_items]

        for item in items:
            title = self._child_text(item, "title", "{http://www.w3.org/2005/Atom}title")
            link = self._child_link(item)

            if title:
                if link:
                    lines.append(f"- [{title}]({link})")
                else:
                    lines.append(f"- {title}")

        return "\n".join(lines)

    def _child_text(self, elem: ET.Element, *tags: str) -> str:
        """Get text from first matching child tag."""
        for tag in tags:
            child = elem.find(tag)
            if child is not None and child.text:
                return child.text.strip()
        return ""

    def _child_link(self, elem: ET.Element) -> str:
        """Extract link from RSS or Atom item."""
        # RSS: <link>url</link>
        link_elem = elem.find("link")
        if link_elem is not None and link_elem.text:
            return link_elem.text.strip()

        # Atom: <link href="url"/>
        link_elem = elem.find("{http://www.w3.org/2005/Atom}link")
        if link_elem is not None:
            return link_elem.get("href", "").strip()

        # RSS: <link> with no text but guid
        guid_elem = elem.find("guid")
        if guid_elem is not None and guid_elem.text:
            return guid_elem.text.strip()

        return ""

    def _detect_feed_type(self, root: ET.Element) -> str:
        """Detect if feed is RSS or Atom."""
        if root.tag.endswith("rss") or root.find("channel") is not None:
            return "rss"
        if "{http://www.w3.org/2005/Atom}" in root.tag:
            return "atom"
        return "unknown"


def _extract_title_from_markdown(content: str) -> str:
    """Extract title from Jina-converted markdown."""
    for line in content.splitlines():
        if line.startswith("Title: "):
            return line.removeprefix("Title: ").strip()
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    return ""
