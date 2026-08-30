"""Tests for the V3 scheduler efficiency fixes.

Covers the three Phase-1 changes from the efficiency-first plan:
  - run_loop always waits the interval, even after a successful enqueue
  - a single failing source is isolated and does not abort the tick
  - skipped_limit no longer double-counts duplicates already passed over
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from knowledge_extractor_v3.config_loader import V3Config
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.scheduler import Scheduler


def _scheduler(tmp_path: Path) -> Scheduler:
    store = QueueStore(tmp_path / "queue.db")
    store.initialize()
    config = V3Config()
    sched = Scheduler(config, queue_store=store)
    return sched


def test_run_loop_always_waits_after_successful_enqueue(tmp_path):
    """The old code only slept when items_enqueued == 0, causing a 6.9x/day
    full-scan storm. After the fix the interval is honoured every tick."""
    sched = _scheduler(tmp_path)
    waits: list[int] = []

    # Force a tick that enqueues nothing (so it would have waited under the old
    # logic too) and a tick that "enqueues" via a patched run_once.
    call_count = {"n": 0}

    def fake_run_once(*a, **kw):
        call_count["n"] += 1
        from knowledge_extractor_v3.scheduler import SchedulerRunResult

        # First tick enqueues 5 items — the exact case the old code skipped the
        # wait for. should_stop stays False so the loop reaches the wait on
        # every tick; max_iterations terminates the loop instead.
        enqueued = 5 if call_count["n"] == 1 else 0
        return SchedulerRunResult(
            sources_processed=10,
            items_discovered=5,
            items_enqueued=enqueued,
            items_skipped_duplicate=0,
            items_skipped_limit=0,
            events=[],
            should_stop=False,
        )

    sched._wait = lambda seconds: waits.append(seconds)  # type: ignore[assignment]

    with patch.object(Scheduler, "run_once", fake_run_once):
        sched.run_loop(interval_seconds=300, max_iterations=2)

    # Both ticks must have waited, including the one that enqueued 5 items.
    assert len(waits) == 2
    assert all(w == 300 for w in waits)


def test_failing_source_does_not_abort_other_sources(tmp_path):
    """One broken RSS feed must not prevent the other sources from enqueuing."""
    sched = _scheduler(tmp_path)

    from knowledge_extractor_v3.sources.models import SourceConfig, SourceItem

    good_source = SourceConfig(id="good", type="rss", url="https://good/feed", enabled=True)
    bad_source = SourceConfig(id="bad", type="rss", url="https://bad/feed", enabled=True)

    good_item = SourceItem(
        source_id="good",
        source_type="rss",
        url="https://good/article-1",
        title="Good article",
    )

    # The registry's discover_items must blow up for "bad" but return the good
    # item for "good". Before the isolation fix this exception killed the tick.
    class StubRegistry:
        def all_enabled_sources(self):
            return [bad_source, good_source]

        def get_adapter(self, _type):
            return None

        def discover_items(self, source, lookback_days=7):
            if source.id == "bad":
                raise RuntimeError("RSS feed exploded")
            return [good_item]

    sched._registry = StubRegistry()  # type: ignore[assignment]

    result = sched.run_once(max_total_items=20)

    # The good source's item still made it into the queue despite bad exploding.
    assert result.items_enqueued == 1
    # The error was recorded as an event, not raised.
    error_events = [e for e in result.events if e.event_type == "error"]
    assert any(e.source_id == "bad" for e in error_events)


def test_skipped_limit_does_not_double_count_duplicates(tmp_path):
    """When the per-tick limit is hit, already-skipped duplicates must not be
    counted again as limit-skipped."""
    sched = _scheduler(tmp_path)

    from knowledge_extractor_v3.sources.models import SourceConfig, SourceItem

    source = SourceConfig(id="s", type="rss", url="https://s/feed", enabled=True)
    # 10 items, all unique urls
    items = [
        SourceItem(source_id="s", source_type="rss", url=f"https://s/a-{i}", title=f"t{i}")
        for i in range(10)
    ]

    class StubRegistry:
        def all_enabled_sources(self):
            return [source]

        def get_adapter(self, _type):
            return None

        def discover_items(self, source, lookback_days=7):
            return items

    sched._registry = StubRegistry()  # type: ignore[assignment]

    # Limit to 3. With 10 unique items: 3 enqueued, 7 skipped by limit.
    result = sched.run_once(max_total_items=3)
    assert result.items_enqueued == 3
    assert result.items_skipped_limit == 7
    assert result.items_skipped_duplicate == 0

    # Now seed 4 duplicates: queue rows plus the in-memory seen-set (the
    # state a previous tick leaves behind). The queue row is authoritative
    # for skipping; the seen-set drives the duplicate accounting. The old
    # code computed skipped_limit as len(all_items) - enqueued (= 10 - 3 =
    # 7), which double-counted the 4 duplicates already passed over. The
    # fix counts only items never reached.
    sched2 = _scheduler(tmp_path)
    sched2._registry = StubRegistry()  # type: ignore[assignment]
    for i in range(4):
        sched2._queue.enqueue(f"https://s/a-{i}", source="s")
        sched2._deduper.mark_seen(f"https://s/a-{i}")

    result2 = sched2.run_once(max_total_items=3)
    # 4 duplicates skipped, then 3 enqueued (a-4,a-5,a-6). The limit check
    # fires at idx=7 (a-7), so remaining 10-7=3 are limit-skipped.
    assert result2.items_skipped_duplicate == 4
    assert result2.items_enqueued == 3
    assert result2.items_skipped_limit == 3  # old code would have reported 7
