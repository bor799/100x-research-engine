"""Obsidian staging writer and dry-run/staging output ports."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Protocol

from ..models import (
    ExtractionResult,
    FetchedContent,
    OutputResult,
    RuntimeMode,
    ScoreResult,
    TypedError,
    utc_now,
)
from ..queue_store import FailureKind, NextAction


class BriefStub:
    """Deterministic stand-in delivery: log the card to a file, no network.

    Successor of the old TelegramStub; the field/file names it feeds
    (telegram_status, telegram_stub.log) are kept so downstream tooling and
    tests stay stable.
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path = Path(log_path) if log_path else Path("dry-run") / "telegram_stub.log"

    def deliver(self, content: FetchedContent, text: str) -> tuple[str, str] | TypedError:
        if content.metadata.get("fixture_scenario") == "output_failed":
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Stub brief delivery failed",
                stage="output.brief_stub",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=content.url,
            )
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
        except OSError:
            pass
        return "stubbed", text[:80]


class OutputPort(Protocol):
    def write(
        self,
        content: FetchedContent,
        score: ScoreResult,
        extraction: ExtractionResult,
        telegram_text: str,
        *,
        prompt_bundle: str,
        prompt_hash: str,
        task_id: int | None,
        runtime_mode: str = "",
        provider_route: str = "",
        is_test_provider: bool = False,
        runtime_fingerprint: str = "",
        wechat_lane: str | None = None,
    ) -> OutputResult:
        ...


class DryRunOutputPort:
    """Return previews and synthetic output IDs without writing files."""

    mode = RuntimeMode.DRY_RUN

    def __init__(self, telegram: BriefStub | None = None) -> None:
        self.telegram = telegram or BriefStub()

    def write(
        self,
        content: FetchedContent,
        score: ScoreResult,
        extraction: ExtractionResult,
        telegram_text: str,
        *,
        prompt_bundle: str,
        prompt_hash: str,
        task_id: int | None,
        runtime_mode: str = "",
        provider_route: str = "",
        is_test_provider: bool = False,
        runtime_fingerprint: str = "",
        wechat_lane: str | None = None,
    ) -> OutputResult:
        delivery = self.telegram.deliver(content, telegram_text)
        if isinstance(delivery, TypedError):
            return OutputResult(ok=False, mode=self.mode, error=delivery)
        telegram_status, telegram_preview = delivery
        task_part = str(task_id) if task_id is not None else "direct"
        return OutputResult(
            ok=True,
            mode=self.mode,
            obsidian_path=f"dry-run://{task_part}/{content.content_hash}",
            telegram_status=telegram_status,
            telegram_preview=telegram_preview,
        )


class StagingObsidianWriter:
    """Write Obsidian markdown into an explicit staging directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def write(
        self,
        content: FetchedContent,
        score: ScoreResult,
        extraction: ExtractionResult,
        *,
        prompt_bundle: str,
        prompt_hash: str,
        runtime_mode: str = "",
        provider_route: str = "",
        is_test_provider: bool = False,
        runtime_fingerprint: str = "",
    ) -> str | TypedError:
        if content.metadata.get("fixture_scenario") == "output_failed":
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Stub Obsidian output failed",
                stage="output.obsidian",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=content.url,
            )

        output_dir = self.root / "obsidian"
        output_dir.mkdir(parents=True, exist_ok=True)
        processed_at = utc_now()
        filename = _filename(processed_at[:10], extraction.title, content.content_hash)
        destination = output_dir / filename
        destination.write_text(
            _render_markdown(
                content,
                score,
                extraction,
                prompt_bundle=prompt_bundle,
                prompt_hash=prompt_hash,
                processed_at=processed_at,
                runtime_mode=runtime_mode,
                provider_route=provider_route,
                is_test_provider=is_test_provider,
                runtime_fingerprint=runtime_fingerprint,
            ),
            encoding="utf-8",
        )
        return str(destination)


class StagingOutputPort:
    """Write markdown to staging and record Telegram delivery as a stub."""

    mode = RuntimeMode.STAGING

    def __init__(self, root: Path, telegram: BriefStub | None = None) -> None:
        self.root = Path(root)
        self.writer = StagingObsidianWriter(self.root)
        self.telegram = telegram or BriefStub(self.root / "telegram_stub.log")

    def write(
        self,
        content: FetchedContent,
        score: ScoreResult,
        extraction: ExtractionResult,
        telegram_text: str,
        *,
        prompt_bundle: str,
        prompt_hash: str,
        task_id: int | None,
        runtime_mode: str = "",
        provider_route: str = "",
        is_test_provider: bool = False,
        runtime_fingerprint: str = "",
        wechat_lane: str | None = None,
    ) -> OutputResult:
        output_path = self.writer.write(
            content,
            score,
            extraction,
            prompt_bundle=prompt_bundle,
            prompt_hash=prompt_hash,
            runtime_mode=runtime_mode,
            provider_route=provider_route,
            is_test_provider=is_test_provider,
            runtime_fingerprint=runtime_fingerprint,
        )
        if isinstance(output_path, TypedError):
            return OutputResult(ok=False, mode=self.mode, error=output_path)

        delivery = self.telegram.deliver(content, telegram_text)
        if isinstance(delivery, TypedError):
            return OutputResult(ok=False, mode=self.mode, obsidian_path=output_path, error=delivery)

        telegram_status, telegram_preview = delivery
        return OutputResult(
            ok=True,
            mode=self.mode,
            obsidian_path=output_path,
            telegram_status=telegram_status,
            telegram_preview=telegram_preview,
        )


def _render_markdown(
    content: FetchedContent,
    score: ScoreResult,
    extraction: ExtractionResult,
    *,
    prompt_bundle: str,
    prompt_hash: str,
    processed_at: str,
    runtime_mode: str = "",
    provider_route: str = "",
    is_test_provider: bool = False,
    runtime_fingerprint: str = "",
    wechat_lane: str = "",
) -> str:
    frontmatter = {
        "title": extraction.title,
        "prompt_bundle": prompt_bundle,
        "prompt_hash": prompt_hash,
        "score": score.score,
        "final_score": score.final_score,
        "signal_tier": score.signal_tier,
        "information_gain": getattr(score, "information_gain", 0.0),
        "action_value": getattr(score, "action_value", 0.0),
        "relevance": getattr(score, "relevance", 0.0),
        "rationale": str(getattr(score, "rationale", ""))[:80],
        "source": content.source,
        "source_type": content.source_type,
        "processed_at": processed_at,
        "url": content.url,
    }
    if runtime_mode:
        frontmatter["runtime_mode"] = runtime_mode
    if provider_route:
        frontmatter["provider_route"] = provider_route
    if wechat_lane:
        # Marks push-lane items so they are distinguishable from archive-only
        # notes; mirrors the lane recorded on the WeChat outbox event.
        frontmatter["wechat_lane"] = wechat_lane
    frontmatter["is_test_provider"] = is_test_provider
    if runtime_fingerprint:
        frontmatter["runtime_fingerprint"] = runtime_fingerprint[:64]
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {_yaml_value(value)}")
    lines.extend(["---", "", extraction.obsidian_brief_markdown.strip(), ""])
    return "\n".join(lines)


def _filename(date_prefix: str, title: str, content_hash: str) -> str:
    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", title.strip()).strip("-").lower()
    safe_title = safe_title[:80] or "untitled"
    return f"{date_prefix}-{safe_title}-{content_hash}.md"


def _yaml_value(value: object) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)
