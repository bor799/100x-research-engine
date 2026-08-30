"""Scheduler same-URL refetch cooldown.

Done tasks re-enter the queue once per cooldown window so the pipeline can
detect same-URL updates; everything else (rejected, in-flight, retry rows,
cooldown 0, dedup disabled) must never re-enter — a rejected row cycling
every window would burn a full absorption call per cooldown for zero value.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from knowledge_extractor_v3.config_loader import V3Config
from knowledge_extractor_v3.queue_store import QueueStatus, QueueStore
from knowledge_extractor_v3.scheduler import Scheduler
from knowledge_extractor_v3.sources.models import SourceItem

URL = "https://example.com/refetch"


def _scheduler(tmp_path: Path, *, cooldown_days: int = 3, enabled: bool = True) -> tuple[Scheduler, QueueStore]:
    store = QueueStore(tmp_path / "queue.db")
    config = V3Config(dedup=replace(V3Config().dedup, enabled=enabled, refetch_cooldown_days=cooldown_days))
    return Scheduler(config, queue_store=store), store


def _item() -> SourceItem:
    return SourceItem(source_id="rss.a", source_type="rss", url=URL, title="t")


def _age_processed_at(store: QueueStore, days: int) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(store.db_path)
    try:
        conn.execute("UPDATE queue SET processed_at = ? WHERE url = ?", (stamp, URL))
        conn.commit()
    finally:
        conn.close()


def test_done_task_past_cooldown_reenters_queue(tmp_path):
    sched, store = _scheduler(tmp_path)
    task = store.enqueue(URL, source="rss.a")
    store.mark_done(task.id, result_title="t", output_path="/tmp/x.md")
    _age_processed_at(store, days=10)

    result = sched._enqueue_item(_item())

    assert result is not None
    row = store.find_by_url(URL)
    assert row is not None and row.status is QueueStatus.PENDING


def test_done_task_inside_cooldown_is_skipped(tmp_path):
    sched, store = _scheduler(tmp_path)
    task = store.enqueue(URL, source="rss.a")
    store.mark_done(task.id, result_title="t", output_path="/tmp/x.md")  # processed_at = now

    assert sched._enqueue_item(_item()) is None


def test_rejected_task_never_reenters(tmp_path):
    sched, store = _scheduler(tmp_path)
    task = store.enqueue(URL, source="rss.a")
    store.mark_rejected(task.id, reason="low quality")
    _age_processed_at(store, days=10)

    assert sched._enqueue_item(_item()) is None


def test_inflight_task_never_reenters(tmp_path):
    sched, store = _scheduler(tmp_path)
    task = store.enqueue(URL, source="rss.a")
    store.mark_processing(task.id, owner="w1")
    _age_processed_at(store, days=10)

    assert sched._enqueue_item(_item()) is None


def test_cooldown_zero_disables_refetch(tmp_path):
    sched, store = _scheduler(tmp_path, cooldown_days=0)
    task = store.enqueue(URL, source="rss.a")
    store.mark_done(task.id, result_title="t", output_path="/tmp/x.md")
    _age_processed_at(store, days=365)

    assert sched._enqueue_item(_item()) is None


def test_dedup_disabled_blocks_refetch(tmp_path):
    sched, store = _scheduler(tmp_path, enabled=False)
    task = store.enqueue(URL, source="rss.a")
    store.mark_done(task.id, result_title="t", output_path="/tmp/x.md")
    _age_processed_at(store, days=365)

    assert sched._enqueue_item(_item()) is None
