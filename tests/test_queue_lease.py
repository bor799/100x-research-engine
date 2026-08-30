"""Lease contract: atomic claims, owner-guarded terminals, heartbeats.

One invariant under test: a task is owned by at most one worker at a time,
only its owner can move it to a terminal state, and an honest owner's
heartbeat keeps the 30-minute stale recovery away.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from knowledge_extractor_v3.queue_store import (
    FailureKind,
    QueueClaimConflict,
    QueueStatus,
    QueueStore,
)


def _two_stores(tmp_path) -> tuple[QueueStore, QueueStore]:
    db = tmp_path / "queue.db"
    return QueueStore(db), QueueStore(db)


def test_atomic_claim_rejects_second_worker(tmp_path):
    a, b = _two_stores(tmp_path)
    task = a.enqueue("https://example.com/race")

    a.mark_processing(task.id, owner="worker-a")
    with pytest.raises(QueueClaimConflict):
        b.mark_processing(task.id, owner="worker-b")

    current = b.get_task(task.id)
    assert current.status is QueueStatus.PROCESSING
    assert current.processing_owner == "worker-a"
    assert current.attempt_count == 1  # the losing claim did not double-count


def test_claim_requires_pending_or_retry(tmp_path):
    a, _ = _two_stores(tmp_path)
    task = a.enqueue("https://example.com/done")
    a.mark_processing(task.id, owner="worker-a")
    a.mark_done(task.id, result_title="t", output_path="/tmp/out.md")

    with pytest.raises(QueueClaimConflict):
        a.mark_processing(task.id, owner="worker-a-again")


def test_zombie_cannot_overwrite_the_winners_terminal_state(tmp_path):
    """The duplicate-file production scenario, replayed at queue level:
    worker A's lease expires, B re-claims and finishes, then A wakes up and
    tries to resurrect the task — the owner guard must refuse."""
    a, b = _two_stores(tmp_path)
    task = a.enqueue("https://example.com/zombie")
    a.mark_processing(task.id, owner="worker-a")

    # A's lease goes stale and is recovered; B legitimately re-claims.
    # (The heartbeat is A's claim-time now, so the recovery cutoff must sit
    # in the future to see it as stale.)
    a.recover_stale_processing((datetime.now(UTC) + timedelta(minutes=5)).isoformat())
    b.mark_processing(task.id, owner="worker-b")

    # Zombie A finishes with a failure and tries to schedule a retry.
    a.schedule_retry(
        task.id,
        failure_kind=FailureKind.UNKNOWN,
        last_error="zombie",
        next_retry_at="2026-09-01T00:00:00+00:00",
    )
    current = a.get_task(task.id)
    assert current.status is QueueStatus.PROCESSING
    assert current.processing_owner == "worker-b"
    assert current.last_error != "zombie"  # A's write was dropped entirely

    # Honest B completes; its terminal write goes through.
    b.mark_done(task.id, result_title="done", output_path="/tmp/out.md")
    assert b.get_task(task.id).status is QueueStatus.DONE


def test_heartbeat_keeps_lease_alive_against_stale_recovery(tmp_path):
    a, _ = _two_stores(tmp_path)
    task = a.enqueue("https://example.com/slow")
    a.mark_processing(task.id, owner="worker-a")

    # Thirty minutes pass, but the worker heartbeated seconds ago.
    a.update_heartbeat(task.id, owner="worker-a")
    recent = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    assert a.recover_stale_processing(recent) == 0
    assert a.get_task(task.id).processing_owner == "worker-a"

    # A truly silent lease is still recovered after the threshold.
    silent = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    assert a.recover_stale_processing(silent) == 1
    assert a.get_task(task.id).status is QueueStatus.RETRY_SCHEDULED


def test_heartbeat_cannot_resurrect_a_recovered_task(tmp_path):
    a, _ = _two_stores(tmp_path)
    task = a.enqueue("https://example.com/late")
    a.mark_processing(task.id, owner="worker-a")
    a.recover_stale_processing((datetime.now(UTC) + timedelta(minutes=5)).isoformat())

    # A late heartbeat from the pre-recovery owner must not touch the row.
    a.update_heartbeat(task.id, owner="worker-a")
    recovered = a.get_task(task.id)
    assert recovered.status is QueueStatus.RETRY_SCHEDULED
    assert recovered.processing_heartbeat_at == ""


def test_store_without_lease_keeps_legacy_terminal_behaviour(tmp_path):
    """Operator tooling (cindy_control reset, requeue_terminal) uses fresh
    store instances with no lease; terminal writes must stay unrestricted."""
    a, _ = _two_stores(tmp_path)
    task = a.enqueue("https://example.com/manual")
    a.mark_processing(task.id, owner="worker-a")

    fresh = QueueStore(tmp_path / "queue.db")
    fresh.mark_done(task.id, result_title="manual", output_path="/tmp/out.md")
    assert fresh.get_task(task.id).status is QueueStatus.DONE
