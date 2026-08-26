"""Live Obsidian writer with atomic writes and optional manifest."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import replace
from pathlib import Path

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
from .obsidian import _filename, _render_markdown


class LiveObsidianWriter:
    """Write Obsidian markdown atomically under the configured vault root.

    Atomic write pattern: write to a temp file in the same directory,
    then os.rename() to the final name. This prevents partial reads.
    """

    def __init__(
        self,
        root: Path,
        *,
        subdir: str = "inbox",
        write_manifest: bool = True,
    ) -> None:
        self.root = Path(root)
        self.subdir = subdir
        self.write_manifest = write_manifest

    def write(
        self,
        content: FetchedContent,
        score: ScoreResult,
        extraction: ExtractionResult,
        *,
        prompt_bundle: str,
        prompt_hash: str,
        task_id: int | None = None,
        runtime_mode: str = "",
        provider_route: str = "",
        is_test_provider: bool = False,
        runtime_fingerprint: str = "",
        wechat_lane: str = "",
    ) -> str | TypedError:
        output_dir = self.root / self.subdir
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message=f"Cannot create output directory: {output_dir}",
                stage="output.obsidian",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=str(exc),
            )

        processed_at = utc_now()
        filename = _filename(processed_at[:10], extraction.title, content.content_hash)
        final_path = output_dir / filename

        # Verify path safety: output stays under root
        try:
            final_path.resolve().relative_to(self.root.resolve())
        except ValueError:
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Output path escapes configured root",
                stage="output.obsidian",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=str(final_path),
            )

        markdown = _render_markdown(
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
            wechat_lane=wechat_lane,
        )

        # Atomic write: temp file in same dir, then rename
        tmp_name = f".tmp-{content.content_hash}-{uuid.uuid4().hex[:8]}.md"
        tmp_path = output_dir / tmp_name
        try:
            tmp_path.write_text(markdown, encoding="utf-8")
            os.rename(tmp_path, final_path)
        except OSError as exc:
            # Clean up temp file if rename failed
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Failed to write Obsidian markdown",
                stage="output.obsidian",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=str(exc),
            )

        # Optional manifest
        if self.write_manifest:
            self._append_manifest(
                output_dir, filename, content, score, prompt_bundle, prompt_hash, task_id,
                runtime_mode=runtime_mode, provider_route=provider_route,
                is_test_provider=is_test_provider, runtime_fingerprint=runtime_fingerprint,
            )

        return str(final_path)

    def _append_manifest(
        self,
        output_dir: Path,
        filename: str,
        content: FetchedContent,
        score: ScoreResult,
        prompt_bundle: str,
        prompt_hash: str,
        task_id: int | None,
        *,
        runtime_mode: str = "",
        provider_route: str = "",
        is_test_provider: bool = False,
        runtime_fingerprint: str = "",
    ) -> None:
        manifest_path = output_dir / "manifest.jsonl"
        entry = {
            "filename": filename,
            "url": content.url,
            "source": content.source,
            "content_hash": content.content_hash,
            "final_score": score.final_score,
            "signal_tier": score.signal_tier,
            "prompt_bundle": prompt_bundle,
            "prompt_hash": prompt_hash,
            "task_id": task_id,
            "timestamp": utc_now(),
        }
        if runtime_mode:
            entry["runtime_mode"] = runtime_mode
        if provider_route:
            entry["provider_route"] = provider_route
        entry["is_test_provider"] = is_test_provider
        if runtime_fingerprint:
            entry["runtime_fingerprint"] = runtime_fingerprint[:64]
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        try:
            with manifest_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # Manifest failure should not block output


class LiveOutputPort:
    """Live output: atomic Obsidian write plus configured message delivery.

    Obsidian is written first, then Telegram. If Telegram is enabled
    and fails, the output is considered failed (not done). If Telegram
    is disabled, Obsidian success alone suffices for ok=True.
    """

    mode = RuntimeMode.LIVE

    def __init__(
        self,
        *,
        obsidian_writer: LiveObsidianWriter,
        wechat_queue: "WechatQueue | None" = None,
    ) -> None:
        self.writer = obsidian_writer
        self.wechat_queue = wechat_queue

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
        # Obsidian first
        output_path = self.writer.write(
            content,
            score,
            extraction,
            prompt_bundle=prompt_bundle,
            prompt_hash=prompt_hash,
            task_id=task_id,
            runtime_mode=runtime_mode,
            provider_route=provider_route,
            is_test_provider=is_test_provider,
            runtime_fingerprint=runtime_fingerprint,
            wechat_lane=wechat_lane or "",
        )
        if isinstance(output_path, TypedError):
            return OutputResult(ok=False, mode=self.mode, error=output_path)

        telegram_status = "not_configured"
        telegram_preview = ""

        wechat_status = ""
        wechat_preview = ""
        # Only push routes (business / strategic lane) reach the WeChat outbox.
        # archive_only content is archived to Obsidian above and intentionally
        # kept out of the user's inbox.
        if self.wechat_queue is not None and wechat_lane:
            queue_content = replace(
                content,
                metadata={
                    **content.metadata,
                    "score": score.score,
                    "final_score": score.final_score,
                    "action_value": getattr(score, "action_value", 0.0),
                    "lane": wechat_lane,
                    "prompt_hash": prompt_hash,
                },
            )
            delivery = self.wechat_queue.deliver(
                queue_content, telegram_text, lane=wechat_lane,
            )
            if isinstance(delivery, TypedError):
                return OutputResult(
                    ok=False,
                    mode=self.mode,
                    obsidian_path=output_path,
                    telegram_status=telegram_status,
                    telegram_preview=telegram_preview,
                    error=delivery,
                )
            wechat_status, wechat_preview = delivery

        return OutputResult(
            ok=True,
            mode=self.mode,
            obsidian_path=output_path,
            telegram_status=telegram_status,
            telegram_preview=telegram_preview,
            wechat_status=wechat_status,
            wechat_preview=wechat_preview,
        )
