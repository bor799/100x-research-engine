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
    LLM_QUOTA_EXHAUSTED = "llm_quota_exhausted"
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
    # Lease/diagnostic fields for orphan task recovery
    "processing_owner",
    "processing_started_at",
    "processing_heartbeat_at",
    "provider_route",
    "last_reply_status",
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
    # Lease/diagnostic fields for orphan task recovery
    processing_owner: str = ""
    processing_started_at: str = ""
    processing_heartbeat_at: str = ""
    provider_route: str = ""
    last_reply_status: str = ""


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
                    processed_at TEXT DEFAULT '',
                    processing_owner TEXT DEFAULT '',
                    processing_started_at TEXT DEFAULT '',
                    processing_heartbeat_at TEXT DEFAULT '',
                    provider_route TEXT DEFAULT '',
                    last_reply_status TEXT DEFAULT ''
                )
                """
            )
            conn.commit()

        # Migrate legacy schema before creating indexes that depend on new columns
        self._migrate_legacy_schema()

        with sqlite3.connect(self.db_path) as conn:
            # Create indexes after migration to ensure all columns exist
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_status_retry "
                "ON queue(status, next_retry_at, priority, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_queue_processing_heartbeat "
                "ON queue(processing_heartbeat_at) WHERE processing_heartbeat_at != ''"
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

    def _migrate_legacy_schema(self) -> None:
        """Add new columns to existing databases without full migration."""
        columns = self.schema_columns()
        new_columns = self.REQUIRED_COLUMNS - columns
        if not new_columns:
            return

        with sqlite3.connect(self.db_path) as conn:
            for col in new_columns:
                try:
                    conn.execute(f"ALTER TABLE queue ADD COLUMN {col} TEXT DEFAULT ''")
                except sqlite3.OperationalError:
                    pass  # Column already exists
            conn.commit()

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
                    status=excluded.status,
                    priority=excluded.priority,
                    attempt_count=0,
                    max_attempts=excluded.max_attempts,
                    next_retry_at='',
                    failure_kind='',
                    last_error='',
                    last_status_detail='',
                    next_action='',
                    result_title='',
                    output_path='',
                    reply_channel=excluded.reply_channel,
                    reply_chat_id=excluded.reply_chat_id,
                    runtime_fingerprint=excluded.runtime_fingerprint,
                    processed_at='',
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

    def mark_processing(self, task_id: int, *, owner: str = "", provider_route: str = "") -> QueueTask:
        self.initialize()
        now = _utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE queue
                SET status=?,
                    attempt_count=attempt_count + 1,
                    failure_kind='',
                    last_error='',
                    last_status_detail='',
                    next_action='',
                    next_retry_at='',
                    processed_at='',
                    processing_owner=?,
                    processing_started_at=?,
                    processing_heartbeat_at=?,
                    provider_route=?,
                    updated_at=?
                WHERE id=?
                """,
                (QueueStatus.PROCESSING.value, owner, now, now, provider_route, now, task_id),
            )
            conn.commit()
        return self.get_task(task_id)

    def update_heartbeat(self, task_id: int, *, owner: str = "") -> QueueTask:
        """Update processing heartbeat to keep task lease alive."""
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE queue
                SET processing_heartbeat_at=?,
                    updated_at=?
                WHERE id=? AND (processing_owner=? OR processing_owner='')
                """,
                (_utc_now(), _utc_now(), task_id, owner),
            )
            conn.commit()
        return self.get_task(task_id)

    def find_stale_leases(self, heartbeat_before: str) -> list[QueueTask]:
        """Find tasks with stale processing leases for recovery."""
        self.initialize()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM queue
                WHERE status = ?
                    AND processing_heartbeat_at != ''
                    AND processing_heartbeat_at < ?
                ORDER BY processing_heartbeat_at ASC
                """,
                (QueueStatus.PROCESSING.value, heartbeat_before),
            ).fetchall()
        return [self._row_to_task(row) for row in rows]

    def mark_done(self, task_id: int, *, result_title: str, output_path: str, provider_route: str = "") -> QueueTask:
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
            provider_route=provider_route,
        )

    def mark_rejected(
        self,
        task_id: int,
        *,
        reason: str,
        detail: str = "",
        failure_kind: FailureKind = FailureKind.VALIDATION_FAILED,
        provider_route: str = "",
    ) -> QueueTask:
        return self._update_status(
            task_id,
            QueueStatus.REJECTED,
            failure_kind=failure_kind,
            next_action=NextAction.DROP,
            last_error=reason,
            last_status_detail=detail,
            processed_at=_utc_now(),
            provider_route=provider_route,
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
        provider_route: str = "",
    ) -> QueueTask:
        if not next_retry_at:
            raise ValueError("retry_scheduled tasks must include next_retry_at")
        current = self.get_task(task_id)
        if current.attempt_count >= current.max_attempts:
            terminal_next_action = (
                NextAction.MANUAL_REVIEW if next_action is NextAction.RETRY_LATER else next_action
            )
            return self.mark_failed_terminal(
                task_id,
                failure_kind=failure_kind,
                last_error=last_error,
                detail=detail,
                next_action=terminal_next_action,
                provider_route=provider_route,
            )
        return self._update_status(
            task_id,
            QueueStatus.RETRY_SCHEDULED,
            failure_kind=failure_kind,
            next_action=next_action,
            last_error=last_error,
            last_status_detail=detail,
            next_retry_at=next_retry_at,
            processed_at="",
            provider_route=provider_route,
        )

    def mark_failed_terminal(
        self,
        task_id: int,
        *,
        failure_kind: FailureKind,
        last_error: str,
        detail: str = "",
        next_action: NextAction = NextAction.MANUAL_REVIEW,
        provider_route: str = "",
    ) -> QueueTask:
        return self._update_status(
            task_id,
            QueueStatus.FAILED_TERMINAL,
            failure_kind=failure_kind,
            next_action=next_action,
            last_error=last_error,
            last_status_detail=detail,
            processed_at=_utc_now(),
            provider_route=provider_route,
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
        last_reply_status: str = "",
        provider_route: str = "",
    ) -> QueueTask:
        self.initialize()
        now = _utc_now()
        with sqlite3.connect(self.db_path) as conn:
            # Build SET clause dynamically to preserve provider_route and last_reply_status
            # when empty values are passed (only update when explicitly provided)
            set_clauses = [
                "status=?",
                "failure_kind=?",
                "last_error=?",
                "last_status_detail=?",
                "next_action=?",
                "result_title=?",
                "output_path=?",
                "next_retry_at=?",
                "processed_at=?",
                "updated_at=?",
            ]
            params = [
                status.value,
                failure_kind.value,
                last_error,
                last_status_detail,
                next_action.value,
                result_title,
                output_path,
                next_retry_at,
                processed_at,
                now,
            ]

            # Only update provider_route if explicitly provided
            if provider_route:
                set_clauses.append("provider_route=?")
                params.append(provider_route)
            else:
                set_clauses.append("provider_route=provider_route")

            # Only update last_reply_status if explicitly provided
            if last_reply_status:
                set_clauses.append("last_reply_status=?")
                params.append(last_reply_status)
            else:
                set_clauses.append("last_reply_status=last_reply_status")

            # Clear lease fields for any non-processing status
            if status != QueueStatus.PROCESSING:
                set_clauses.extend([
                    "processing_owner=''",
                    "processing_started_at=''",
                    "processing_heartbeat_at=''",
                ])
            else:
                set_clauses.extend([
                    "processing_owner=processing_owner",
                    "processing_started_at=processing_started_at",
                    "processing_heartbeat_at=processing_heartbeat_at",
                ])

            set_clause = ", ".join(set_clauses)
            params.append(task_id)

            conn.execute(
                f"UPDATE queue SET {set_clause} WHERE id=?",
                params,
            )
            conn.commit()
        return self.get_task(task_id)

    @staticmethod
    def _row_to_task(row: Optional[sqlite3.Row | tuple]) -> QueueTask:
        if row is None:
            raise KeyError("Queue task row not found")
        # Support both legacy (20 cols) and new (25 cols) schemas
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
            processing_owner=row[20] or "" if len(row) > 20 else "",
            processing_started_at=row[21] or "" if len(row) > 21 else "",
            processing_heartbeat_at=row[22] or "" if len(row) > 22 else "",
            provider_route=row[23] or "" if len(row) > 23 else "",
            last_reply_status=row[24] or "" if len(row) > 24 else "",
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
            ready_query = """
                SELECT * FROM queue
                WHERE status = ?
                    OR (
                        status = ?
                        AND (next_retry_at = '' OR next_retry_at <= ?)
                    )
                ORDER BY priority ASC, id ASC
                LIMIT ?
            """
            rows = conn.execute(
                ready_query,
                (QueueStatus.PENDING.value, QueueStatus.RETRY_SCHEDULED.value, now_ts, limit),
            ).fetchall()

        return [self._row_to_task(row) for row in rows]

    def recover_stale_processing(self, before: str) -> int:
        """Recover tasks stuck in processing status back to retry_scheduled.

        Prioritizes processing_heartbeat_at for staleness detection; falls back
        to updated_at for legacy tasks without heartbeat data.

        Returns count of tasks recovered.
        """
        self.initialize()
        now = _utc_now()

        with sqlite3.connect(self.db_path) as conn:
            # First recover tasks with stale heartbeat (preferred for new schema)
            cursor = conn.execute(
                """
                UPDATE queue
                SET status = ?,
                    next_retry_at = ?,
                    updated_at = ?,
                    processing_owner = '',
                    processing_started_at = '',
                    processing_heartbeat_at = ''
                WHERE status = ?
                    AND processing_heartbeat_at != ''
                    AND processing_heartbeat_at < ?
                """,
                (QueueStatus.RETRY_SCHEDULED.value, now, now, QueueStatus.PROCESSING.value, before),
            )
            recovered_heartbeat = cursor.rowcount

            # Then recover legacy tasks without heartbeat (using updated_at)
            cursor = conn.execute(
                """
                UPDATE queue
                SET status = ?,
                    next_retry_at = ?,
                    updated_at = ?,
                    processing_owner = '',
                    processing_started_at = ''
                WHERE status = ?
                    AND processing_heartbeat_at = ''
                    AND updated_at < ?
                """,
                (QueueStatus.RETRY_SCHEDULED.value, now, now, QueueStatus.PROCESSING.value, before),
            )
            recovered_legacy = cursor.rowcount

            conn.commit()
            return recovered_heartbeat + recovered_legacy

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

    def update_reply_status(self, task_id: int, status: str) -> QueueTask:
        """Update only last_reply_status and updated_at without changing task state."""
        self.initialize()
        now = _utc_now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE queue
                SET last_reply_status=?,
                    updated_at=?
                WHERE id=?
                """,
                (status, now, task_id),
            )
            conn.commit()
        return self.get_task(task_id)
