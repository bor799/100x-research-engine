"""Source discovery package for V3 scheduler."""

from .models import SourceConfig, SchedulerEvent, SourceItem
from .registry import SourceAdapter, SourceRegistry
from .rss import RSSAdapter
from .url_list import URLListAdapter
from .search import SearchAdapter, create_search_source
from .dedupe import URLDeduper, normalize_url


__all__ = [
    "SourceConfig",
    "SchedulerEvent",
    "SourceItem",
    "SourceAdapter",
    "SourceRegistry",
    "RSSAdapter",
    "URLListAdapter",
    "SearchAdapter",
    "create_search_source",
    "URLDeduper",
    "normalize_url",
]
