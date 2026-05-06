"""Fetcher contracts and Phase 2/3 implementations."""

from .fixture import FixtureFetcher
from .web import WebPageFetcher
from .http_client import HttpClient, create_http_client, DEFAULT_USER_AGENT
from .router import FetcherRouter

try:
    from .multi_channel import AgentReachFetcher
    from .search import SearchChannelAdapter, create_search_adapter
    __all__ = [
        "FixtureFetcher",
        "WebPageFetcher",
        "FetcherRouter",
        "AgentReachFetcher",
        "SearchChannelAdapter",
        "create_search_adapter",
        "HttpClient",
        "create_http_client",
        "DEFAULT_USER_AGENT",
    ]
except ImportError:
    # Agent Reach 依赖 V2 代码，在某些环境下可能不可用
    __all__ = [
        "FixtureFetcher",
        "WebPageFetcher",
        "FetcherRouter",
        "HttpClient",
        "create_http_client",
        "DEFAULT_USER_AGENT",
    ]
