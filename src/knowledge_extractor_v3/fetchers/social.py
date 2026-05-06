"""Social platform channel adapters.

Provides adapters for:
- Reddit (posts, comments)
- V2EX (discussions)
- Hacker News (threads)

These platforms can be fetched via Jina Reader but benefit from
platform-specific metadata extraction.
"""

from __future__ import annotations

import re
from typing import Any

from .http_client import HttpClient, create_http_client
from ..models import FetchedContent, TypedError


class RedditChannelAdapter:
    """Reddit channel adapter.

    Handles:
    - Post URLs: reddit.com/r/subreddit/comments/xyz/post_title/
    - User profiles: reddit.com/user/username/
    - Subreddit: reddit.com/r/subreddit/

    Uses Jina Reader for content extraction.
    """

    domains = {
        "reddit.com",
        "www.reddit.com",
        "old.reddit.com",
        "new.reddit.com",
    }

    @property
    def name(self) -> str:
        return "reddit"

    def can_handle(self, url: str) -> bool:
        domain = self._domain(url)
        return any(domain == d or domain.endswith(f".{d}") for d in self.domains)

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(timeout=int(config.get("timeout", 30)))

        response = client.get_via_jina(url)
        if isinstance(response, TypedError) or not response.is_success:
            return None

        # Extract Reddit-specific metadata
        metadata = self._extract_reddit_metadata(response.content, url)

        return {
            "title": metadata.get("title") or _title_from_jina(response.content) or url[:100],
            "content": _body_from_jina(response.content),
            "source": "agent-reach-reddit",
            "author": metadata.get("author", ""),
            "metadata": {
                "via_jina": True,
                "subreddit": metadata.get("subreddit", ""),
                "score": metadata.get("score", ""),
                "comments": metadata.get("comments", ""),
                "raw_url": url,
            },
        }

    def check(self, config: dict[str, Any]) -> str:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(timeout=5, max_retries=1)
        result = client.get_via_jina("https://reddit.com")
        return "ok" if not isinstance(result, TypedError) and result.is_success else "error"

    def _extract_reddit_metadata(self, content: str, url: str) -> dict[str, str]:
        """Extract metadata from Reddit content."""
        metadata = {}

        # Extract subreddit from URL
        subreddit_match = re.search(r"/r/([^/]+)", url)
        if subreddit_match:
            metadata["subreddit"] = subreddit_match.group(1)

        # Try to extract from content (Jina sometimes includes metadata)
        title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
        if title_match:
            metadata["title"] = title_match.group(1).strip()

        # Look for score patterns in content
        score_match = re.search(r"(\d+(?:\.\d+[kK])?)\s+points?", content, re.IGNORECASE)
        if score_match:
            metadata["score"] = score_match.group(1)

        # Look for comment count
        comments_match = re.search(r"(\d+(?:\.\d+[kK])?)\s+comments?", content, re.IGNORECASE)
        if comments_match:
            metadata["comments"] = comments_match.group(1)

        # Look for author
        author_match = re.search(r"u/([\w-]+)|@([\w-]+)", content)
        if author_match:
            metadata["author"] = author_match.group(1) or author_match.group(2)

        return metadata

    @staticmethod
    def _domain(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().removeprefix("www.")


class V2EXChannelAdapter:
    """V2EX channel adapter.

    V2EX is a Chinese developer community.
    Handles:
    - Thread URLs: v2ex.com/t/xyz
    - Node pages: v2ex.com/go/nodeName

    Uses Jina Reader for content extraction.
    """

    domains = {
        "v2ex.com",
        "www.v2ex.com",
    }

    @property
    def name(self) -> str:
        return "v2ex"

    def can_handle(self, url: str) -> bool:
        domain = self._domain(url)
        return any(domain == d or domain.endswith(f".{d}") for d in self.domains)

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(timeout=int(config.get("timeout", 30)))

        response = client.get_via_jina(url)
        if isinstance(response, TypedError) or not response.is_success:
            return None

        # Extract V2EX-specific metadata
        metadata = self._extract_v2ex_metadata(response.content, url)

        return {
            "title": metadata.get("title") or _title_from_jina(response.content) or url[:100],
            "content": _body_from_jina(response.content),
            "source": "agent-reach-v2ex",
            "author": metadata.get("author", ""),
            "metadata": {
                "via_jina": True,
                "node": metadata.get("node", ""),
                "replies": metadata.get("replies", ""),
                "raw_url": url,
            },
        }

    def check(self, config: dict[str, Any]) -> str:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(timeout=5, max_retries=1)
        result = client.get_via_jina("https://v2ex.com")
        return "ok" if not isinstance(result, TypedError) and result.is_success else "error"

    def _extract_v2ex_metadata(self, content: str, url: str) -> dict[str, str]:
        """Extract metadata from V2EX content."""
        metadata = {}

        # Extract node from URL (e.g., /go/python)
        node_match = re.search(r"/go/([^/]+)|/t/\d+", url)
        if node_match:
            node = node_match.group(1)
            if node:
                metadata["node"] = node

        # Extract title (usually first h1)
        title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
        if title_match:
            metadata["title"] = title_match.group(1).strip()

        # Look for reply count
        replies_match = re.search(r"(\d+)\s+回复", content)
        if replies_match:
            metadata["replies"] = replies_match.group(1)

        # Look for author (V2EX format)
        author_match = re.search(r"@([\w]+)", content[:500])  # Check early in content
        if author_match:
            metadata["author"] = author_match.group(1)

        return metadata

    @staticmethod
    def _domain(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().removeprefix("www.")


class HackerNewsChannelAdapter:
    """Hacker News channel adapter.

    Handles:
    - Item URLs: news.ycombinator.com/item?id=xyz
    - User profiles: news.ycombinator.com/user?id=username
    - Front page: news.ycombinator.com/

    Uses Jina Reader for content extraction.
    """

    domains = {
        "news.ycombinator.com",
        "hn.algolia.com",  # Algolia search for HN
    }

    @property
    def name(self) -> str:
        return "hackernews"

    def can_handle(self, url: str) -> bool:
        domain = self._domain(url)
        return any(domain == d or domain.endswith(f".{d}") for d in self.domains)

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(timeout=int(config.get("timeout", 30)))

        response = client.get_via_jina(url)
        if isinstance(response, TypedError) or not response.is_success:
            return None

        metadata = self._extract_hn_metadata(response.content, url)

        return {
            "title": metadata.get("title") or _title_from_jina(response.content) or url[:100],
            "content": _body_from_jina(response.content),
            "source": "agent-reach-hackernews",
            "author": metadata.get("author", ""),
            "metadata": {
                "via_jina": True,
                "points": metadata.get("points", ""),
                "comments": metadata.get("comments", ""),
                "raw_url": url,
            },
        }

    def check(self, config: dict[str, Any]) -> str:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(timeout=5, max_retries=1)
        result = client.get_via_jina("https://news.ycombinator.com")
        return "ok" if not isinstance(result, TypedError) and result.is_success else "error"

    def _extract_hn_metadata(self, content: str, url: str) -> dict[str, str]:
        """Extract metadata from HN content."""
        metadata = {}

        # Look for points
        points_match = re.search(r"(\d+)\s+points?", content, re.IGNORECASE)
        if points_match:
            metadata["points"] = points_match.group(1)

        # Look for comments
        comments_match = re.search(r"(\d+)\s+comments?", content, re.IGNORECASE)
        if comments_match:
            metadata["comments"] = comments_match.group(1)

        # Look for author
        author_match = re.search(r"@?([\w]+)\s+(\d+\s+|points?)", content[:1000])
        if author_match:
            metadata["author"] = author_match.group(1)

        return metadata

    @staticmethod
    def _domain(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower().removeprefix("www.")


def _title_from_jina(content: str) -> str:
    """Extract title from Jina Reader response."""
    for line in content.splitlines():
        if line.startswith("Title: "):
            return line.removeprefix("Title: ").strip()
    return ""


def _body_from_jina(content: str) -> str:
    """Extract body from Jina Reader response."""
    marker = "Markdown Content:"
    if marker in content:
        return content.split(marker, 1)[1].strip()
    return content.strip()
