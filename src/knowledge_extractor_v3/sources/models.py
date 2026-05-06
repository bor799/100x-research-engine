"""Source models for V3 scheduler.

Defines SourceItem (discovered content from a source) and SourceConfig
(source configuration loaded from config file).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceItem:
    """A single item discovered from a source.

    Represents one RSS entry, one URL from a list, etc.
    """

    source_id: str
    source_type: str
    url: str
    title: str = ""
    published_at: str = ""
    author: str = ""
    priority: int = 100
    reply_channel: str = ""
    reply_chat_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_priority(self, priority: int) -> "SourceItem":
        """Return a copy with different priority."""
        return SourceItem(
            source_id=self.source_id,
            source_type=self.source_type,
            url=self.url,
            title=self.title,
            published_at=self.published_at,
            author=self.author,
            priority=priority,
            reply_channel=self.reply_channel,
            reply_chat_id=self.reply_chat_id,
            metadata=dict(self.metadata),
        )


@dataclass(frozen=True)
class SourceConfig:
    """Configuration for a single source."""

    id: str
    type: str  # "rss", "url_list", etc.
    url: str = ""
    enabled: bool = True
    priority: int = 100
    tags: list[str] = field(default_factory=list)
    path: str = ""  # For url_list sources
    max_items: int = 10  # Per-tick limit
    lookback_days: int = 7
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SchedulerEvent:
    """Event logged by scheduler."""

    timestamp: str
    source_id: str
    event_type: str  # "discovered", "skipped", "error"
    count: int = 0
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
