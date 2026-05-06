"""Telegram output stub for Phase 2 staging."""

from __future__ import annotations

from pathlib import Path

from ..models import FetchedContent, TypedError, utc_now
from ..queue_store import FailureKind, NextAction


class TelegramStub:
    """Record Telegram output intentions without network access."""

    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path = Path(log_path) if log_path is not None else None
        self.records: list[dict[str, str]] = []

    def deliver(self, content: FetchedContent, text: str) -> tuple[str, str] | TypedError:
        if content.metadata.get("fixture_scenario") == "output_failed":
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Stub Telegram output failed",
                stage="output.telegram",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=content.url,
            )

        preview = text.strip()
        record = {
            "created_at": utc_now(),
            "url": content.url,
            "title": content.title,
            "preview": preview,
        }
        self.records.append(record)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text(
                "\n".join(self._format_record(item) for item in self.records) + "\n",
                encoding="utf-8",
            )
        return "stubbed", preview

    @staticmethod
    def _format_record(record: dict[str, str]) -> str:
        return (
            f"[{record['created_at']}] {record['title']}\n"
            f"URL: {record['url']}\n"
            f"{record['preview']}\n"
        )
