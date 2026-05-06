import sqlite3

import pytest

from knowledge_extractor_v3.queue_store import (
    FailureKind,
    NextAction,
    QueueStatus,
    QueueStore,
)


def test_queue_status_values_are_fixed():
    assert [status.value for status in QueueStatus] == [
        "pending",
        "processing",
        "retry_scheduled",
        "done",
        "rejected",
        "failed_terminal",
    ]


def test_queue_store_initializes_required_schema_in_tmp_path(tmp_path):
    db_path = tmp_path / ".100x_v3" / "queue.db"
    store = QueueStore(db_path)

    store.initialize()

    assert ".100x_v2" not in str(db_path)
    assert QueueStore.REQUIRED_COLUMNS <= store.schema_columns()


def test_queue_store_refuses_v2_queue_path(tmp_path):
    with pytest.raises(ValueError, match="V2 queue"):
        QueueStore(tmp_path / ".100x_v2" / "queue.db")


def test_queue_terminal_and_retry_states_are_distinct(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="fp-test")

    done = store.enqueue("https://example.com/done", source="telegram")
    done = store.mark_done(done.id, result_title="Done", output_path="/tmp/v3-demo.md")

    rejected = store.enqueue("https://example.com/rejected", source="rss")
    rejected = store.mark_rejected(rejected.id, reason="not an investment signal")

    retry = store.enqueue("https://example.com/retry", source="rss")
    retry = store.schedule_retry(
        retry.id,
        failure_kind=FailureKind.LLM_RATE_LIMIT,
        last_error="429 provider rate limit",
        next_retry_at="2026-04-28T12:00:00+00:00",
    )

    failed = store.enqueue("https://example.com/failed", source="telegram")
    failed = store.mark_failed_terminal(
        failed.id,
        failure_kind=FailureKind.OUTPUT_FAILED,
        last_error="Telegram push failed after max attempts",
    )

    assert done.status is QueueStatus.DONE
    assert done.failure_kind is FailureKind.NONE
    assert done.next_action is NextAction.NONE
    assert done.output_path == "/tmp/v3-demo.md"

    assert rejected.status is QueueStatus.REJECTED
    assert rejected.failure_kind is FailureKind.VALIDATION_FAILED
    assert rejected.next_action is NextAction.DROP

    assert retry.status is QueueStatus.RETRY_SCHEDULED
    assert retry.failure_kind is FailureKind.LLM_RATE_LIMIT
    assert retry.next_action is NextAction.RETRY_LATER
    assert retry.next_retry_at == "2026-04-28T12:00:00+00:00"

    assert failed.status is QueueStatus.FAILED_TERMINAL
    assert failed.failure_kind is FailureKind.OUTPUT_FAILED
    assert failed.next_action is NextAction.MANUAL_REVIEW


def test_mark_done_requires_output_path(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db")
    task = store.enqueue("https://example.com/no-output")

    with pytest.raises(ValueError, match="output_path"):
        store.mark_done(task.id, result_title="No output", output_path="")


def test_queue_database_used_by_tests_is_tmp_path_only(tmp_path):
    db_path = tmp_path / ".100x_v3" / "queue.db"
    store = QueueStore(db_path)
    store.enqueue("https://example.com/tmp-only")

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]

    assert count == 1
    assert str(db_path).startswith(str(tmp_path))
    assert ".100x_v2" not in str(db_path)
