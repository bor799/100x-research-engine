from knowledge_extractor_v3.fetchers.router import FetcherRouter
from knowledge_extractor_v3.models import FetchedContent, sha256_text


class RecordingFetcher:
    def __init__(self, name: str):
        self.name = name
        self.urls: list[str] = []

    def fetch(self, url: str):
        self.urls.append(url)
        text = f"{self.name} fetched {url}"
        return FetchedContent(
            url=url,
            source=self.name,
            source_type=self.name,
            title=self.name,
            text=text,
            fetched_at="2026-04-29T00:00:00+00:00",
            content_hash=sha256_text(text),
        )


def test_router_sends_fixture_urls_to_fixture_fetcher():
    fixture = RecordingFetcher("fixture")
    router = FetcherRouter(
        fixture_fetcher=fixture,
        web_fetcher=RecordingFetcher("web"),
        agent_reach_fetcher=RecordingFetcher("agent"),
    )

    result = router.fetch("fixture://high_signal")

    assert result.source == "fixture"
    assert fixture.urls == ["fixture://high_signal"]


def test_router_sends_special_platforms_to_agent_reach():
    agent = RecordingFetcher("agent")
    router = FetcherRouter(
        fixture_fetcher=RecordingFetcher("fixture"),
        web_fetcher=RecordingFetcher("web"),
        agent_reach_fetcher=agent,
    )

    result = router.fetch("https://x.com/example/status/1")

    assert result.source == "agent"
    assert agent.urls == ["https://x.com/example/status/1"]


def test_router_sends_normal_http_to_agent_reach():
    agent = RecordingFetcher("agent")
    router = FetcherRouter(
        fixture_fetcher=RecordingFetcher("fixture"),
        web_fetcher=RecordingFetcher("web"),
        agent_reach_fetcher=agent,
    )

    result = router.fetch("https://example.com/article")

    assert result.source == "agent"
    assert agent.urls == ["https://example.com/article"]


def test_router_uses_web_fetcher_as_legacy_agent_reach_fallback():
    web = RecordingFetcher("web")
    router = FetcherRouter(
        fixture_fetcher=RecordingFetcher("fixture"),
        web_fetcher=web,
    )

    result = router.fetch("https://example.com/article")

    assert result.source == "web"
    assert web.urls == ["https://example.com/article"]
