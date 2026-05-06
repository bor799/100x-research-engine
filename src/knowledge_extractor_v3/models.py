"""Shared Phase 2 data models for the V3 dry-run/staging core."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

# Python 3.11+ compatibility
UTC = timezone.utc

from .queue_store import FailureKind, NextAction, QueueStatus


class RuntimeMode(str, Enum):
    DRY_RUN = "dry_run"
    STAGING = "staging"
    LIVE = "live"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def retry_at(minutes: int = 15) -> str:
    return (datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=minutes)).isoformat()


def sha256_text(value: str, *, length: int = 16) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


@dataclass(frozen=True)
class FetchedContent:
    url: str
    source: str
    source_type: str
    title: str
    text: str
    fetched_at: str
    content_hash: str
    raw: str = ""
    author: str = ""
    published_at: str = ""
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class TypedError:
    failure_kind: FailureKind
    message: str
    stage: str
    retryable: bool
    next_action: NextAction
    detail: str = ""
    next_retry_at: str = ""


@dataclass(frozen=True)
class StageResult:
    stage: str
    ok: bool
    started_at: str
    ended_at: str
    duration_ms: int
    error: TypedError | None = None
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoreResult:
    prompt_bundle: str
    prompt_hash: str
    model_route: str
    raw_text: str
    parsed: dict[str, object]
    score: float
    final_score: float
    signal_tier: str
    decision_window_status: str
    source_type: str
    source_tier: str
    interest_flag: str
    attribution_chain: object


@dataclass(frozen=True)
class ExtractionResult:
    prompt_bundle: str
    prompt_hash: str
    model_route: str
    raw_text: str
    parsed: dict[str, object]
    title: str
    one_line_signal: str
    obsidian_brief_markdown: str


@dataclass(frozen=True)
class OutputResult:
    ok: bool
    mode: RuntimeMode
    obsidian_path: str = ""
    telegram_status: str = ""
    telegram_preview: str = ""
    error: TypedError | None = None


@dataclass(frozen=True)
class PromptRunResult:
    prompt_bundle: str
    prompt_hash: str
    ok: bool
    score_result: ScoreResult | None = None
    extraction_result: ExtractionResult | None = None
    error: TypedError | None = None


@dataclass(frozen=True)
class ProcessResult:
    url: str
    source: str
    queue_task_id: int | None
    current_stage: str
    final_status: QueueStatus
    retryable: bool
    failure_kind: FailureKind
    next_action: NextAction
    output_path: str
    telegram_status: str
    prompt_bundle: str
    stage_results: list[StageResult] = field(default_factory=list)
    score_result: ScoreResult | None = None
    extraction_result: ExtractionResult | None = None
    parallel_results: list[PromptRunResult] = field(default_factory=list)
    error: TypedError | None = None
