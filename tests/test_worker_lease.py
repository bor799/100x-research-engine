"""Worker-side lease behaviour: claim races, heartbeat thread, batch cuts."""

from __future__ import annotations

import sqlite3
import threading
import time

from knowledge_extractor_v3.config_loader import V3Config
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.worker import QueueWorker, WorkerConfig


def _worker(store: QueueStore, **cfg) -> QueueWorker:
    return QueueWorker(
        V3Config(),
        queue_store=store,
        worker_config=WorkerConfig(batch_size=5, log_jsonl=False, **cfg),
    )


def test_lost_claim_race_skips_task_without_counting_failure(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    task = store.enqueue("https://example.com/taken")
    rival = QueueStore(tmp_path / "queue.db")
    rival.mark_processing(task.id, owner="rival-stack")

    result = _worker(store).run_once()

    assert result.tasks_processed == 0
    assert result.tasks_succeeded == 0
    assert result.tasks_failed == 0
    assert result.consecutive_failures == 0  # a race is not a failure
    assert store.get_task(task.id).processing_owner == "rival-stack"


def test_shutdown_check_cuts_batch_before_first_task(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    store.enqueue("https://example.com/one")
    store.enqueue("https://example.com/two")

    worker = _worker(store, shutdown_check=lambda: True)
    result = worker.run_once()

    assert result.tasks_processed == 0
    # Both tasks stay pending — nothing was claimed by the silenced worker.
    assert store.count_by_status()["pending"] == 2


def test_heartbeat_loop_refreshes_the_lease_while_running(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    task = store.enqueue("https://example.com/slow")
    worker = _worker(store, heartbeat_interval_seconds=1.0)
    store.mark_processing(task.id, owner="worker-hb")

    # Age the heartbeat as if minutes of pipeline work had passed silently.
    stale = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE queue SET processing_heartbeat_at=? WHERE id=?", (stale, task.id)
        )
        conn.commit()

    stop = threading.Event()
    thread = threading.Thread(
        target=worker._heartbeat_loop, args=(task.id, "worker-hb", stop), daemon=True
    )
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.get_task(task.id).processing_heartbeat_at != stale:
                break
            time.sleep(0.05)
    finally:
        stop.set()
        thread.join(timeout=3)

    assert store.get_task(task.id).processing_heartbeat_at != stale
    assert not thread.is_alive()  # the loop exits promptly on stop


def test_worker_does_not_install_signal_handlers_when_daemon_owns_them(tmp_path):
    import signal

    store = QueueStore(tmp_path / "queue.db")
    previous = signal.getsignal(signal.SIGTERM)
    try:
        sentinel = lambda *a: None  # noqa: E731
        signal.signal(signal.SIGTERM, sentinel)
        _worker(store, manage_signals=False)
        assert signal.getsignal(signal.SIGTERM) is sentinel  # untouched

        _worker(store, manage_signals=True)
        assert signal.getsignal(signal.SIGTERM) is not sentinel  # standalone default
    finally:
        signal.signal(signal.SIGTERM, previous)
