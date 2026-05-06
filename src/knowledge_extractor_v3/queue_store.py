"""Queue Store contract for 100X Knowledge Extractor V3.

This module is intentionally small in phase 1. It defines the durable queue
state machine and a minimal SQLite implementation that tests can exercise
without touching V2 runtime state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Optional

UTC = timezone.utc


class QueueStoreError(RuntimeError):
    """Base error for queue-store contract violations."""


class QueueStoreSchemaError(QueueStoreError):
    """Raised when an existing queue database does not match the V3 schema."""


class QueueStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_SCHEDULED = "retry_scheduled"
    DONE = "done"
    REJECTED = "rejected"
    FAILED_TERMINAL = "failed_terminal"


class FailureKind(str, Enum):
    NONE = ""
    FETCH_FAILED = "fetch_failed"
    AUTH_INVALID = "auth_invalid"
    CONTENT_BLOCKED = "content_blocked"
    FETCH_TIMEOUT = "fetch_timeout"
    VALIDATION_FAILED = "validation_failed"
    PARSE_ERROR = "parse_error"
    LLM_RATE_LIMIT = "llm_rate_limit"
    LLM_TIMEOUT = "llm_timeout"
    OUTPUT_FAILED = "output_failed"
    RUNTIME_GUARD = "runtime_guard"
    UNKNOWN = "unknown"


class NextAction(str, Enum):
    NONE = ""
    RETRY_LATER = "retry_later"
    MANUAL_REVIEW = "manual_review"
    AUTH_REFRESH_REQUIRED = "auth_refresh_required"
    DROP = "drop"
    INVESTIGATE = "investigate"


QUEUE_REQUIRED_COLUMNS = {
    "id",
    "url",
    "source",
    "status",
    "priority",
    "attempt_count",
    "max_attempts",
    "next_retry_at",
    "failure_kind",
    "last_error",
    "last_status_detail",
    "next_action",
    "result_title",
    "output_path",
    "reply_channel",
    "reply_chat_id",
    "runtime_fingerprint",
    "created_at",
    "updated_at",
    "processed_at",
}


@dataclass(frozen=True)
class QueueTask:
    id: int
    url: str
    source: str = ""
    status: QueueStatus = QueueStatus.PENDING
    priority: int = 100
    attempt_count: int = 0
    max_attempts: int = 3
    next_retry_at: str = ""
    failure_kind: FailureKind = FailureKind.NONE
    last_error: str = ""
    last_status_detail: str = ""
    next_action: NextAction = NextAction.NONE
    result_title: str = ""
    output_path: str = ""
    reply_channel: str = ""
    reply_chat_id: str = ""
    runtime_fingerprint: str = ""
    created_at: str = ""
    updated_at: str = ""
    processed_at: str = ""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _contains_v2_marker(path: Path) -> bool:
    text = str(path.expanduser())
    return ".100x_v2" in text or "/knowledge-extractor/v2" in text


def _enum_values(values: Iterable[Enum]) -> tuple[str, ...]:
    return tuple(item.value for item in values)


class QueueStore:
    """Minimal SQLite-backed V3 queue store."""

    REQUIRED_COLUMNS = QUEUE_REQUIRED_COLUMNS
    STATUS_VALUES = _enum_values(QueueStatus)

    def __init__(self, db_path: Path, *, runtime_fingerprint: str = "") -> None:
        self.db_path = Path(db_path).expanduser()
        self.runtime_fingerprint = runtime_fingerprint
        if _contains_v2_marker(self.db_path):
            raise ValueError(f"Refusing to use V2 queue path: {self.db_path}")

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    source TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    priority INTEGER NOT NULL DEFAULT 100,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    next_retry_at TEXT DEFAULT '',
                    failure_kind TEXT DEFAULT '',
                    last_error TEXT DEFAULT '',
                    last_status_detail TEXT DEFAULT '',
                    next_action TEXT DEFAULT '',
                    result_title TEXT DEFAULT '',
                    output_path TEXT DEFAULT '',
                    reply_channel TEXT DEFAULT '',
                    reply_chat_id TEXT DEFAULT '',
                    runtime_fingerprint TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    processed_at TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_status_retry "
                "ON queue(status, next_retry_at, priority, id)"
            )
            conn.commit()
        self.validate_schema()

    def schema_columns(self) -> set[str]:
        if not self.db_path.exists():
            return set()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("PRAGMA table_info(queue)").fetchall()
        return {row[1] for row in rows}

    def validate_schema(self) -> None:
        columns = self.schema_columns()
        missing = self.REQUIRED_COLUMNS - columns
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise QueueStoreSchemaError(f"V3 queue schema is missing columns: {missing_list}")

    def enqueue(
        self,
        url: str,
        *,
        source: str = "",
        priority: int = 100,
        max_attempts: int = 3,
        reply_channel: str = "",
        reply_chat_id: str = "",
    ) -> QueueTask:
        self.initialize()
        now = _utc_now()
        normalized_url = url.strip()
        if not normalized_url:
            raise ValueError("Queue URL cannot be empty")

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO queue (
                    url, source, status, priority, max_attempts,
                    reply_channel, reply_chat_id, runtime_fingerprint,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    source=excluded.source,
                    priority=excluded.priority,
                    max_attempts=excluded.max_attempts,
                    reply_channel=excluded.reply_channel,
                    reply_chat_id=excluded.reply_chat_id,
                    updated_at=excluded.updated_at
                """,
                (
                    normalized_url,
                    source,
                    QueueStatus.PENDING.value,
                    priority,
                    max_attempts,
                    reply_channel,
                    reply_chat_id,
                    self.runtime_fingerprint,
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM queue WHERE url=?", (normalized_url,)).fetchone()
        return self._row_to_task(row)

    def get_task(self, task_id: int) -> QueueTask:
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM queue WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(f"Queue task not found: {task_id}")
        return self._row_to_task(row)

    def mark_processing(self, task_id: int) -> QueueTask:
        return self._update_status(task_id, QueueStatus.PROCESSING, processed_at="")

    def mark_done(self, task_id: int, *, result_title: str, output_path: str) -> QueueTask:
        if not output_path:
            raise ValueError("Done tasks must include an output_path proving the output loop closed")
        return self._update_status(
            task_id,
            QueueStatus.DONE,
            failure_kind=FailureKind.NONE,
            next_action=NextAction.NONE,
            result_title=result_title,
            output_path=output_path,
            processed_at=_utc_now(),
        )

    def mark_rejected(
        self,
        task_id: int,
        *,
        reason: str,
        detail: str = "",
        failure_kind: FailureKind = FailureKind.VALIDATION_FAILED,
    ) -> QueueTask:
        return self._update_status(
            task_id,
            QueueStatus.REJECTED,
            failure_kind=failure_kind,
            next_action=NextAction.DROP,
            last_error=reason,
            last_status_detail=detail,
            processed_at=_utc_now(),
        )

    def schedule_retry(
        self,
        task_id: int,
        *,
        failure_kind: FailureKind,
        last_error: str,
        next_retry_at: str,
        next_action: NextAction = NextAction.RETRY_LATER,
        detail: str = "",
    ) -> QueueTask:
        if not next_retry_at:
            raise ValueError("retry_scheduled tasks must include next_retry_at")
        return self._update_status(
            task_id,
            QueueStatus.RETRY_SCHEDULED,
            failure_kind=failure_kind,
            next_action=next_action,
            last_error=last_error,
            last_status_detail=detail,
            next_retry_at=next_retry_at,
            processed_at="",
        )

    def mark_failed_terminal(
        self,
        task_id: int,
        *,
        failure_kind: FailureKind,
        last_error: str,
        detail: str = "",
        next_action: NextAction = NextAction.MANUAL_REVIEW,
    ) -> QueueTask:
        return self._update_status(
            task_id,
            QueueStatus.FAILED_TERMINAL,
            failure_kind=failure_kind,
            next_action=next_action,
            last_error=last_error,
            last_status_detail=detail,
            processed_at=_utc_now(),
        )

    def _update_status(
        self,
        task_id: int,
        status: QueueStatus,
        *,
        failure_kind: FailureKind = FailureKind.NONE,
        next_action: NextAction = NextAction.NONE,
        last_error: str = "",
        last_status_detail: str = "",
        result_title: str = "",
        output_path: str = "",
        next_retry_at: str = "",
        processed_at: str = "",
    ) -> QueueTask:
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE queue
                SET status=?,
                    failure_kind=?,
                    last_error=?,
                    last_status_detail=?,
                    next_action=?,
                    result_title=?,
                    output_path=?,
                    next_retry_at=?,
                    processed_at=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    status.value,
                    failure_kind.value,
                    last_error,
                    last_status_detail,
                    next_action.value,
                    result_title,
                    output_path,
                    next_retry_at,
                    processed_at,
                    _utc_now(),
                    task_id,
                ),
            )
            conn.commit()
        return self.get_task(task_id)

    @staticmethod
    def _row_to_task(row: Optional[sqlite3.Row | tuple]) -> QueueTask:
        if row is None:
            raise KeyError("Queue task row not found")
        return QueueTask(
            id=row[0],
            url=row[1],
            source=row[2] or "",
            status=QueueStatus(row[3]),
            priority=row[4],
            attempt_count=row[5],
            max_attempts=row[6],
            next_retry_at=row[7] or "",
            failure_kind=FailureKind(row[8] or ""),
            last_error=row[9] or "",
            last_status_detail=row[10] or "",
            next_action=NextAction(row[11] or ""),
            result_title=row[12] or "",
            output_path=row[13] or "",
            reply_channel=row[14] or "",
            reply_chat_id=row[15] or "",
            runtime_fingerprint=row[16] or "",
            created_at=row[17] or "",
            updated_at=row[18] or "",
            processed_at=row[19] or "",
        )

    # -- Helper methods for Phase 4 worker -----------------------------------

    def next_ready_tasks(self, limit: int, now: str = "") -> list[QueueTask]:
        """Fetch next pending or retry_scheduled tasks ready for processing.

        Orders by priority (lower first), then id for stability.
        For retry_scheduled, only includes tasks where next_retry_at <= now.
        """
        self.initialize()
        now_ts = now or _utc_now()

        with sqlite3.connect(self.db_path) as conn:
            # pending tasks (no retry time check needed)
            pending_query = """
                SELECT * FROM queue
                WHERE status = ?
                ORDER BY priority ASC, id ASC
                LIMIT ?
            """
            pending_rows = conn.execute(pending_query, (QueueStatus.PENDING.value, limit)).fetchall()

            # retry_scheduled tasks that are ready
            retry_query = """
                SELECT * FROM queue
                WHERE status = ?
                    AND (next_retry_at = '' OR next_retry_at <= ?)
                ORDER BY priority ASC, id ASC
                LIMIT ?
            """
            retry_rows = conn.execute(retry_query, (QueueStatus.RETRY_SCHEDULED.value, now_ts, limit)).fetchall()

        # Combine, preferring pending over retry at same priority
        tasks = [self._row_to_task(row) for row in pending_rows]
        retry_tasks = [self._row_to_task(row) for row in retry_rows]

        # Merge preserving order: interleave by priority
        # For simplicity, just append retry tasks after pending
        tasks.extend(retry_tasks)

        # Respect limit
        return tasks[:limit]

    def recover_stale_processing(self, before: str) -> int:
        """Recover tasks stuck in processing status back to retry_scheduled.

        Returns count of tasks recovered.
        """
        self.initialize()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE queue
                SET status = ?,
                    next_retry_at = ?,
                    updated_at = ?
                WHERE status = ?
                    AND updated_at < ?
                """,
                (QueueStatus.RETRY_SCHEDULED.value, before, _utc_now(), QueueStatus.PROCESSING.value, before),
            )
            conn.commit()
            return cursor.rowcount

    def count_by_status(self) -> dict[str, int]:
        """Return count of tasks by status."""
        self.initialize()

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM queue GROUP BY status"
            ).fetchall()

        return {row[0]: row[1] for row in rows}

    def find_by_url(self, url: str) -> QueueTask | None:
        """Find task by normalized URL."""
        self.initialize()

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM queue WHERE url=?", (url.strip(),)).fetchone()

        if row is None:
            return None
        return self._row_to_task(row)
