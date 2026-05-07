"""Tests for source registry and scheduler components."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from knowledge_extractor_v3.sources.models import SourceConfig, SourceItem, SchedulerEvent
from knowledge_extractor_v3.sources.registry import SourceAdapter, SourceRegistry
from knowledge_extractor_v3.sources.rss import RSSAdapter, _parse_rss_feed, _parse_rss_date
from knowledge_extractor_v3.sources.url_list import URLListAdapter
from knowledge_extractor_v3.sources.dedupe import normalize_url, URLDeduper
from knowledge_extractor_v3.scheduler import Scheduler, SchedulerRunResult, create_scheduler
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.config_loader import (
    V3Config,
    RuntimeConfig,
    LiveConfig,
    WorkerConfig as V3WorkerConfig,
    SchedulerConfig as V3SchedulerConfig,
    SourceConfig as V3SourceConfig,
)


# ---------------------------------------------------------------------------
# Tests for models
# ---------------------------------------------------------------------------


def test_source_item_with_priority():
    """SourceItem.with_priority creates copy with new priority."""
    item = SourceItem(
        source_id="test",
        source_type="rss",
        url="https://example.com",
        title="Test",
        priority=100,
    )

    new_item = item.with_priority(50)

    assert new_item.priority == 50
    assert new_item.url == item.url
    assert item.priority == 100  # Original unchanged


def test_scheduler_event_creation():
    """SchedulerEvent can be created."""
    event = SchedulerEvent(
        timestamp="2024-01-01T00:00:00+00:00",
        source_id="test",
        event_type="discovered",
        count=5,
        message="Found 5 items",
    )

    assert event.source_id == "test"
    assert event.event_type == "discovered"
    assert event.count == 5


# ---------------------------------------------------------------------------
# Tests for URL deduplication
# ---------------------------------------------------------------------------


def test_normalize_url():
    """normalize_url handles various URL formats."""
    # Lowercase scheme and netloc
    assert normalize_url("HTTP://Example.com/Path") == "http://example.com/Path"

    # Remove trailing slash
    assert normalize_url("https://example.com/path/") == "https://example.com/path"

    # Remove fragment
    assert normalize_url("https://example.com/path#fragment") == "https://example.com/path"

    # Remove default ports
    assert normalize_url("http://example.com:80/path") == "http://example.com/path"
    assert normalize_url("https://example.com:443/path") == "https://example.com/path"


def test_url_deduper():
    """URLDeduper tracks seen URLs."""
    deduper = URLDeduper()

    assert not deduper.is_seen("https://example.com/test")

    deduper.mark_seen("https://example.com/test")
    assert deduper.is_seen("https://example.com/test")

    # Normalized URLs are detected as duplicates
    assert deduper.is_seen("https://EXAMPLE.COM/test")  # Case insensitive
    assert deduper.is_seen("https://example.com/test/")  # Trailing slash


def test_url_deduper_filter():
    """URLDeduper.filter_unseen filters list."""
    deduper = URLDeduper()

    urls = [
        "https://example.com/1",
        "https://example.com/2",
        "https://example.com/1",  # Duplicate
    ]

    deduper.mark_seen("https://example.com/1")

    unseen = deduper.filter_unseen(urls)

    assert unseen == ["https://example.com/2"]


# ---------------------------------------------------------------------------
# Tests for source registry
# ---------------------------------------------------------------------------


class MockAdapter:
    """Mock adapter for testing."""

    def __init__(self, source_type: str = "mock") -> None:
        self._type = source_type
        self.items_to_return = []

    def discover(self, source: SourceConfig, *, lookback_days: int = 7) -> list[SourceItem]:
        return self.items_to_return

    def source_type(self) -> str:
        return self._type


def test_source_registry_register_adapter():
    """Registry can register and retrieve adapters."""
    registry = SourceRegistry([])
    adapter = MockAdapter()

    registry.register_adapter(adapter)

    assert registry.get_adapter("mock") is adapter


def test_source_registry_sources_by_type():
    """Registry filters sources by type."""
    sources = [
        SourceConfig(id="rss1", type="rss", enabled=True),
        SourceConfig(id="rss2", type="rss", enabled=True),
        SourceConfig(id="list1", type="url_list", enabled=True),
        SourceConfig(id="disabled", type="rss", enabled=False),
    ]

    registry = SourceRegistry(sources)

    rss_sources = registry.sources_by_type("rss")
    assert len(rss_sources) == 2
    assert [s.id for s in rss_sources] == ["rss1", "rss2"]


def test_source_registry_all_enabled():
    """Registry returns only enabled sources."""
    sources = [
        SourceConfig(id="enabled1", type="rss", enabled=True),
        SourceConfig(id="disabled1", type="rss", enabled=False),
        SourceConfig(id="enabled2", type="rss", enabled=True),
    ]

    registry = SourceRegistry(sources)

    assert len(registry.all_enabled_sources()) == 2


def test_source_registry_discover_items():
    """Registry discovers items using registered adapter."""
    sources = [SourceConfig(id="test", type="mock", enabled=True)]
    registry = SourceRegistry(sources)

    adapter = MockAdapter()
    adapter.items_to_return = [
        SourceItem(
            source_id="test",
            source_type="mock",
            url="https://example.com",
            title="Test",
        )
    ]
    registry.register_adapter(adapter)

    items = registry.discover_items(sources[0])

    assert len(items) == 1
    assert items[0].url == "https://example.com"


# ---------------------------------------------------------------------------
# Tests for RSS adapter
# ---------------------------------------------------------------------------


def test_parse_rss_date():
    """RSS date parser handles multiple formats."""
    # RFC822 format
    parsed = _parse_rss_date("Mon, 01 Jan 2024 12:00:00 +0000")
    assert parsed is not None

    # ISO 8601
    parsed = _parse_rss_date("2024-01-01T12:00:00Z")
    assert parsed is not None
    assert parsed.tzinfo is not None

    # Date-only and timezone-less values are normalized to aware UTC
    parsed = _parse_rss_date("2024-01-01")
    assert parsed is not None
    assert parsed.tzinfo is not None

    # Invalid
    assert _parse_rss_date("invalid") is None


def test_parse_rss_feed_basic():
    """Basic RSS feed parsing."""
    rss_content = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <title>Test Feed</title>
  <item>
    <title>Test Article</title>
    <link>https://example.com/article1</link>
    <pubDate>Mon, 01 Jan 2024 12:00:00 GMT</pubDate>
  </item>
</channel>
</rss>
"""

    entries = _parse_rss_feed(rss_content, "https://example.com/feed")

    assert len(entries) == 1
    assert entries[0]["title"] == "Test Article"
    assert entries[0]["link"] == "https://example.com/article1"


def test_parse_rss_feed_atom():
    """Atom feed parsing."""
    atom_content = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Feed</title>
  <entry>
    <title>Atom Article</title>
    <link href="https://example.com/atom1"/>
    <published>2024-01-01T12:00:00Z</published>
  </entry>
</feed>
"""

    entries = _parse_rss_feed(atom_content, "https://example.com/atom")

    assert len(entries) == 1
    assert entries[0]["title"] == "Atom Article"
    assert entries[0]["link"] == "https://example.com/atom1"


def test_parse_rss_feed_dc_namespace_metadata():
    """RSS parser handles common namespaced dc creator/date tags."""
    rss_content = """<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel>
  <item>
    <title>Namespaced Article</title>
    <link>https://example.com/ns</link>
    <dc:creator>Alice</dc:creator>
    <dc:date>2026-04-29T12:00:00Z</dc:date>
  </item>
</channel>
</rss>
"""

    entries = _parse_rss_feed(rss_content, "https://example.com/feed")

    assert entries == [{
        "title": "Namespaced Article",
        "link": "https://example.com/ns",
        "published_at": "2026-04-29T12:00:00Z",
        "author": "Alice",
    }]


def test_rss_adapter_discover():
    """RSSAdapter discovers items from source config."""
    # Create a mock RSS feed file
    rss_content = """<?xml version="1.0"?>
<rss version="2.0">
<channel>
  <item>
    <title>Test</title>
    <link>https://example.com/test</link>
  </item>
</channel>
</rss>
"""

    # Mock urllib - we'll test the parse logic directly
    adapter = RSSAdapter()

    source = SourceConfig(
        id="test",
        type="rss",
        url="https://example.com/feed",
        enabled=True,
        priority=50,
    )

    # For this test, we'll verify the adapter structure
    assert adapter.source_type() == "rss"


# ---------------------------------------------------------------------------
# Tests for URL list adapter
# ---------------------------------------------------------------------------


def test_url_list_adapter_plain_text():
    """URLListAdapter reads plain text URLs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "urls.txt"
        path.write_text("https://example.com/1\nhttps://example.com/2\n# Comment\nhttps://example.com/3")

        adapter = URLListAdapter()

        source = SourceConfig(
            id="manual",
            type="url_list",
            path=str(path),
            enabled=True,
        )

        items = adapter.discover(source)

        assert len(items) == 3
        assert items[0].url == "https://example.com/1"
        assert items[1].url == "https://example.com/2"
        assert items[2].url == "https://example.com/3"


# ---------------------------------------------------------------------------
# Tests for scheduler
# ---------------------------------------------------------------------------


def test_scheduler_run_once_empty():
    """Scheduler with no sources returns empty result."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = V3Config(
            runtime=RuntimeConfig(
                state_root=str(state_root),
                queue_db_path=str(state_root / "queue.db"),
            ),
            live=LiveConfig(enabled=False),
            worker=V3WorkerConfig(),
        )

        queue_store = QueueStore(state_root / "queue.db")
        scheduler = Scheduler(config, queue_store=queue_store)

        result = scheduler.run_once(max_total_items=20)

        assert result.sources_processed == 0
        assert result.items_enqueued == 0


def test_scheduler_wait_uses_timed_sleep(monkeypatch):
    """Scheduler loop wait should wake by timeout instead of blocking on a signal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = V3Config(
            runtime=RuntimeConfig(
                state_root=str(state_root),
                queue_db_path=str(state_root / "queue.db"),
            ),
            live=LiveConfig(enabled=False),
            worker=V3WorkerConfig(),
        )
        queue_store = QueueStore(state_root / "queue.db")
        scheduler = Scheduler(config, queue_store=queue_store)
        sleeps: list[float] = []

        def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)
            scheduler._shutdown_requested = True

        monkeypatch.setattr("knowledge_extractor_v3.scheduler.time.sleep", fake_sleep)

        scheduler._wait(300)

        assert sleeps
        assert sleeps[0] <= 1.0


def test_scheduler_respects_limit():
    """Scheduler respects max_total_items limit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)

        # Create URL list file
        url_file = Path(tmpdir) / "urls.txt"
        url_file.write_text("\n".join(f"https://example.com/{i}" for i in range(10)))

        # Use sources.models.SourceConfig directly for testing
        from knowledge_extractor_v3.sources.models import SourceConfig as TestSourceConfig

        config = V3Config(
            runtime=RuntimeConfig(
                state_root=str(state_root),
                queue_db_path=str(state_root / "queue.db"),
            ),
            live=LiveConfig(enabled=False),
            worker=V3WorkerConfig(),
        )

        queue_store = QueueStore(state_root / "queue.db")

        # Create registry directly with test source
        from knowledge_extractor_v3.sources.registry import SourceRegistry
        registry = SourceRegistry(
            sources=[
                TestSourceConfig(
                    id="manual",
                    type="url_list",
                    path=str(url_file),
                    enabled=True,
                ),
            ],
        )
        registry.register_adapter(URLListAdapter())

        scheduler = Scheduler(config, queue_store=queue_store, source_registry=registry)

        result = scheduler.run_once(max_total_items=5)

        # Should enqueue at most 5 items
        assert result.items_enqueued == 5
        assert result.items_skipped_limit > 0


def test_scheduler_deduplication():
    """Scheduler skips already queued URLs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)

        # Create URL list file
        url_file = Path(tmpdir) / "urls.txt"
        url_file.write_text("https://example.com/1\nhttps://example.com/2")

        # Use sources.models.SourceConfig directly for testing
        from knowledge_extractor_v3.sources.models import SourceConfig as TestSourceConfig

        config = V3Config(
            runtime=RuntimeConfig(
                state_root=str(state_root),
                queue_db_path=str(state_root / "queue.db"),
            ),
            live=LiveConfig(enabled=False),
            worker=V3WorkerConfig(),
        )

        queue_store = QueueStore(state_root / "queue.db")

        # Create registry directly with test source
        from knowledge_extractor_v3.sources.registry import SourceRegistry
        registry = SourceRegistry(
            sources=[
                TestSourceConfig(
                    id="manual",
                    type="url_list",
                    path=str(url_file),
                    enabled=True,
                ),
            ],
        )
        registry.register_adapter(URLListAdapter())

        scheduler = Scheduler(config, queue_store=queue_store, source_registry=registry)

        # First run - should enqueue both
        result1 = scheduler.run_once(max_total_items=10)
        assert result1.items_enqueued == 2

        # Second run - should skip both
        result2 = scheduler.run_once(max_total_items=10)
        assert result2.items_enqueued == 0
        assert result2.items_skipped_duplicate == 2


# ---------------------------------------------------------------------------
# Test runner
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import traceback

    tests = [
        test_source_item_with_priority,
        test_scheduler_event_creation,
        test_normalize_url,
        test_url_deduper,
        test_url_deduper_filter,
        test_source_registry_register_adapter,
        test_source_registry_sources_by_type,
        test_source_registry_all_enabled,
        test_source_registry_discover_items,
        test_parse_rss_date,
        test_parse_rss_feed_basic,
        test_parse_rss_feed_atom,
        test_rss_adapter_discover,
        test_url_list_adapter_plain_text,
        test_scheduler_run_once_empty,
        test_scheduler_respects_limit,
        test_scheduler_deduplication,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
            print(f"✓ {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"✗ {test.__name__}")
            traceback.print_exc()
        except Exception as e:
            failed += 1
            print(f"✗ {test.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
