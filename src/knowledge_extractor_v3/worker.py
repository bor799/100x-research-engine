"""Queue worker for processing tasks through the V3 pipeline.

Worker processes ready tasks from the queue:
- Runtime Guard check on startup
- Recover stale processing tasks
- Process tasks by priority
- Batch size limit per run
- Consecutive failure limit
- JSONL logging
- SIGINT/SIGTERM graceful shutdown
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from .config_loader import ConfigLoader, V3Config
from .fetchers.base import Fetcher
from .fetchers.multi_channel import AgentReachFetcher
from .fetchers.router import FetcherRouter
from .fetchers.web import WebPageFetcher
from .live_gate import LiveGate
from .llm.provider import LLMProvider, StubLLMProvider
from .models import FetchedContent, ProcessResult, QueueStatus, RuntimeMode, TypedError, retry_at, utc_now
from .pipeline import Pipeline
from .prompt_registry import PromptRegistry
from .queue_store import FailureKind, NextAction, QueueStore, QueueTask
from .runtime_guard import RuntimeGuard, RuntimeGuardError, RuntimePaths, resolve_runtime_paths


class LiveModeUnavailable(RuntimeError):
    """Raised when LIVE was requested but the live gate does not pass."""


class TelegramReplyClient(Protocol):
    def deliver(
        self,
        content: FetchedContent,
        text: str,
        *,
        chat_id: str | None = None,
    ) -> tuple[str, str] | TypedError:
        ...


# ---------------------------------------------------------------------------
# Worker state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerRunResult:
    """Result of a single worker run."""

    tasks_processed: int
    tasks_succeeded: int
    tasks_failed: int
    tasks_recovered: int
    consecutive_failures: int
    should_stop: bool


@dataclass
class WorkerState:
    """Mutable state for worker execution."""

    consecutive_failures: int = 0
    total_processed: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    shutdown_requested: bool = False

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.total_processed += 1
        self.total_succeeded += 1

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.total_processed += 1
        self.total_failed += 1


# ---------------------------------------------------------------------------
# Worker configuration
# ---------------------------------------------------------------------------


@dataclass
class WorkerConfig:
    """Worker runtime configuration."""

    batch_size: int = 10
    max_consecutive_failures: int = 5
    processing_stale_after_minutes: int = 30
    log_jsonl: bool = True
    mode: RuntimeMode = RuntimeMode.STAGING


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class QueueWorker:
    """Process queue tasks through the V3 pipeline.

    Worker can run in --once mode (process one batch) or --loop mode.
    Supports graceful shutdown on SIGINT/SIGTERM.
    """

    def __init__(
        self,
        config: V3Config,
        *,
        queue_store: QueueStore,
        fetcher: Fetcher | None = None,
        llm_provider: LLMProvider | None = None,
        prompt_registry: PromptRegistry | None = None,
        worker_config: WorkerConfig | None = None,
        log_path: Path | None = None,
        reply_telegram_client: TelegramReplyClient | None = None,
    ) -> None:
        self._config = config
        self._queue = queue_store
        self._fetcher = fetcher or _create_default_fetcher(config)
        self._llm = llm_provider or StubLLMProvider()
        self._prompts = prompt_registry or PromptRegistry.from_config(Path.cwd(), config.prompts)
        self._worker_cfg = worker_config or WorkerConfig()
        self._log_path = log_path
        self._reply_telegram_client = reply_telegram_client

        # Setup signal handlers for graceful shutdown
        self._state = WorkerState()
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        # Track provider availability for rate limiting
        self._providers_exhausted_until = 0.0

    # -- Main entry points ---------------------------------------------------

    def run_once(self) -> WorkerRunResult:
        """Run a single batch of tasks and return results."""
        # Recover stale processing tasks
        recovered = self._recover_stale_tasks()

        # Check if providers are exhausted
        if self._providers_exhausted_until > time.time():
            return WorkerRunResult(
                tasks_processed=0,
                tasks_succeeded=0,
                tasks_failed=0,
                tasks_recovered=recovered,
                consecutive_failures=self._state.consecutive_failures,
                should_stop=False,
            )

        # Fetch ready tasks
        tasks = self._queue.next_ready_tasks(
            limit=self._worker_cfg.batch_size,
            now=utc_now(),
        )

        if not tasks:
            return WorkerRunResult(
                tasks_processed=0,
                tasks_succeeded=0,
                tasks_failed=0,
                tasks_recovered=recovered,
                consecutive_failures=self._state.consecutive_failures,
                should_stop=self._state.shutdown_requested,
            )

        rate_limited = False
        providers_exhausted = False

        # Process each task
        for task in tasks:
            if self._state.shutdown_requested:
                break

            if self._state.consecutive_failures >= self._worker_cfg.max_consecutive_failures:
                break

            result = self._process_task(task)
            if result.final_status in (QueueStatus.DONE, QueueStatus.REJECTED):
                self._state.record_success()
            else:
                self._state.record_failure()

            self._log_result(result, task)
            if result.failure_kind in (FailureKind.LLM_RATE_LIMIT, FailureKind.LLM_QUOTA_EXHAUSTED):
                # Check if this is a "all providers exhausted" error
                error_msg = result.error.message if result.error else ""
                if "all" in error_msg.lower() or "exhausted" in error_msg.lower():
                    providers_exhausted = True
                    # Set cooldown for 15 minutes
                    self._providers_exhausted_until = time.time() + 900
                else:
                    rate_limited = True
                break

        return WorkerRunResult(
            tasks_processed=self._state.total_processed,
            tasks_succeeded=self._state.total_succeeded,
            tasks_failed=self._state.total_failed,
            tasks_recovered=recovered,
            consecutive_failures=self._state.consecutive_failures,
            should_stop=(
                self._state.shutdown_requested
                or rate_limited
                or providers_exhausted
                or self._state.consecutive_failures >= self._worker_cfg.max_consecutive_failures
            ),
        )

    def run_loop(
        self,
        *,
        poll_interval_seconds: int = 30,
        max_iterations: int = 0,
    ) -> WorkerRunResult:
        """Run worker in a loop until shutdown or max iterations.

        Args:
            poll_interval_seconds: Seconds to wait between batches.
            max_iterations: Maximum number of batch iterations (0 = unlimited).
        """
        iteration = 0
        while not self._state.shutdown_requested:
            if max_iterations > 0 and iteration >= max_iterations:
                break

            result = self.run_once()
            iteration += 1

            if result.should_stop:
                break

            if result.tasks_processed == 0:
                # No tasks to process, wait before next poll
                self._wait(poll_interval_seconds)

        return WorkerRunResult(
            tasks_processed=self._state.total_processed,
            tasks_succeeded=self._state.total_succeeded,
            tasks_failed=self._state.total_failed,
            tasks_recovered=0,  # Already counted in individual runs
            consecutive_failures=self._state.consecutive_failures,
            should_stop=self._state.shutdown_requested,
        )

    # -- Task processing -----------------------------------------------------

    def _process_task(self, task: QueueTask) -> ProcessResult:
        """Process a single task through the pipeline."""
        import socket

        # Determine mode first (may raise LiveModeUnavailable)
        # Do NOT claim the task yet - Live gate check happens before claim
        mode = self._determine_runtime_mode()

        # Eagerly swap to live provider before claiming so provider_route is correct.
        if mode is RuntimeMode.LIVE and not str(getattr(self._llm, "model_route", "")).startswith("live://"):
            from .llm.live_provider import create_live_provider
            self._llm = create_live_provider(self._config.llm, env=os.environ)

        # Now that we know the mode is valid, claim the task
        owner = f"{socket.gethostname()}-{os.getpid()}"
        provider_route = str(getattr(self._llm, "model_route", ""))
        task = self._queue.mark_processing(task.id, owner=owner, provider_route=provider_route)

        try:
            return self._process_task_impl(task, owner, mode)
        except LiveModeUnavailable:
            # This should not happen since we checked mode above, but handle it
            # Release the lease and keep task in retry_scheduled
            self._queue.schedule_retry(
                task.id,
                failure_kind=FailureKind.RUNTIME_GUARD,
                last_error="Live mode became unavailable after initial check",
                next_retry_at=retry_at(5),
                detail="",
                provider_route=provider_route,
            )
            raise
        except Exception as exc:
            # Unhandled exception - recover task to retry_scheduled
            import traceback
            error_msg = f"Unhandled exception: {exc}"
            detail = traceback.format_exc()[-500:]

            try:
                self._queue.schedule_retry(
                    task.id,
                    failure_kind=FailureKind.UNKNOWN,
                    last_error=error_msg,
                    next_retry_at=retry_at(5),
                    detail=detail,
                    provider_route=provider_route,
                )
            except Exception:
                pass  # Best effort recovery

            return ProcessResult(
                url=task.url,
                source=task.source,
                queue_task_id=task.id,
                current_stage="processing",
                final_status=QueueStatus.RETRY_SCHEDULED,
                retryable=True,
                failure_kind=FailureKind.UNKNOWN,
                next_action=NextAction.RETRY_LATER,
                output_path="",
                telegram_status="",
                prompt_bundle="",
                error=TypedError(
                    failure_kind=FailureKind.UNKNOWN,
                    message=error_msg,
                    stage="processing",
                    retryable=True,
                    next_action=NextAction.RETRY_LATER,
                    detail=detail,
                ),
            )

    def _process_task_impl(self, task: QueueTask, owner: str, mode: RuntimeMode) -> ProcessResult:
        """Inner processing logic for a single task."""
        # Build pipeline with appropriate output port
        pipeline = Pipeline(
            queue_store=self._queue,
            fetcher=self._fetcher,
            llm_provider=self._llm,
            prompt_registry=self._prompts,
            staging_root=self._queue.db_path.parent / "staging",
            reject_threshold=self._config.score_gate.reject_threshold,
            score_gate_enabled=self._config.score_gate.enabled,
            live_output=None,  # Will be set by mode if needed
        )

        # For live mode, the pipeline needs a live output port
        # This is handled by Pipeline._output_port() based on mode
        if mode is RuntimeMode.LIVE:
            from .llm.live_provider import create_live_provider
            from .outputs.live_obsidian import LiveOutputPort, LiveObsidianWriter
            from .outputs.telegram_live import LiveTelegramClient
            from .config_loader import ConfigLoader

            loader = ConfigLoader(project_root=Path.cwd())
            obsidian_root = loader.expand_path(self._config.outputs.obsidian_root)

            writer = LiveObsidianWriter(
                root=obsidian_root,
                subdir=self._config.outputs.obsidian_subdir,
                write_manifest=self._config.outputs.write_manifest,
            )

            telegram = None
            if self._config.outputs.telegram_enabled:
                # 优先使用直接配置的 token/chat_id，其次使用环境变量
                token = self._config.outputs.telegram_bot_token or loader.resolve_env(self._config.outputs.telegram_bot_token_env)
                chat_id = self._config.outputs.telegram_admin_chat_id or loader.resolve_env(self._config.outputs.telegram_admin_chat_id_env)
                if token and chat_id:
                    telegram = LiveTelegramClient(
                        bot_token=token,
                        chat_id=chat_id,
                        enabled=True,
                    )

            live_output = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)

            # Replace the pipeline's live output
            pipeline._live_output = live_output

            # Live provider already created in _process_task before claiming.
            pipeline.llm_provider = self._llm

        result = pipeline.process_url(
            task.url,
            source=task.source,
            queue_task_id=task.id,
            mode=mode,
            claim_task=False,  # Already claimed by worker
        )
        reply_status = self._notify_reply_if_needed(task, result, mode)
        if reply_status and not result.telegram_status:
            result = replace(result, telegram_status=reply_status)
        return result

    def _determine_runtime_mode(self) -> RuntimeMode:
        """Determine runtime mode based on config and live gate."""
        if self._worker_cfg.mode is not RuntimeMode.LIVE:
            return self._worker_cfg.mode

        if not self._config.live.enabled:
            raise LiveModeUnavailable("live.enabled is false in config")

        loader = ConfigLoader(project_root=Path.cwd())
        loader.load()
        guard = RuntimeGuard.from_env(project_root=Path.cwd())
        gate = LiveGate(self._config, config_loader=loader, runtime_guard=guard)

        result = gate.check()
        if result.passed:
            return RuntimeMode.LIVE
        reasons = "; ".join(result.rejection_reasons)
        raise LiveModeUnavailable(f"live gate failed: {reasons}")

    def _notify_reply_if_needed(
        self,
        task: QueueTask,
        result: ProcessResult,
        mode: RuntimeMode,
    ) -> str:
        """Close the Telegram loop for manual tasks that do not reach live output."""
        if task.reply_channel != "telegram" or not task.reply_chat_id:
            return ""
        if result.telegram_status == "sent":
            return ""

        should_notify = result.final_status in {
            QueueStatus.RETRY_SCHEDULED,
            QueueStatus.REJECTED,
            QueueStatus.FAILED_TERMINAL,
        }
        if result.final_status is QueueStatus.DONE and result.telegram_status not in {"sent", ""}:
            should_notify = True
        if not should_notify:
            return ""

        client = self._reply_client(mode)
        if client is None:
            # No client available, mark as reply_failed
            self._queue.update_reply_status(task.id, "reply_failed_no_client")
            return "reply_failed_no_client"

        updated_task = self._queue.get_task(task.id)
        message = _format_reply_status(updated_task, result)
        placeholder = FetchedContent(
            url=result.url,
            source=result.source,
            source_type="queue_status",
            title=updated_task.result_title or result.current_stage,
            text=message,
            fetched_at=utc_now(),
            content_hash=str(updated_task.id),
        )
        delivery = client.deliver(placeholder, message, chat_id=task.reply_chat_id)
        if isinstance(delivery, TypedError):
            # Persist reply failure status
            self._queue.update_reply_status(task.id, "reply_failed")
            return "reply_failed"
        status, _preview = delivery
        # Persist reply success status
        self._queue.update_reply_status(task.id, f"reply_{status}")
        return f"reply_{status}"

    def _reply_client(self, mode: RuntimeMode) -> TelegramReplyClient | None:
        if self._reply_telegram_client is not None:
            return self._reply_telegram_client
        if mode is not RuntimeMode.LIVE or not self._config.outputs.telegram_enabled:
            return None

        from .outputs.telegram_live import LiveTelegramClient

        # 优先使用直接配置的 token/chat_id，其次使用环境变量
        token = self._config.outputs.telegram_bot_token
        chat_id = self._config.outputs.telegram_admin_chat_id
        if not token or not chat_id:
            loader = ConfigLoader(project_root=Path.cwd())
            token = loader.resolve_env(self._config.outputs.telegram_bot_token_env)
            chat_id = loader.resolve_env(self._config.outputs.telegram_admin_chat_id_env)
        if not token or not chat_id:
            return None
        return LiveTelegramClient(bot_token=token, chat_id=chat_id, enabled=True)

    # -- Recovery ------------------------------------------------------------

    def _recover_stale_tasks(self) -> int:
        """Recover tasks stuck in processing status."""
        stale_threshold = (
            datetime.now(UTC).replace(microsecond=0) -
            timedelta(minutes=self._worker_cfg.processing_stale_after_minutes)
        ).isoformat()

        return self._queue.recover_stale_processing(stale_threshold)

    # -- Logging ------------------------------------------------------------

    def _log_result(self, result: ProcessResult, task: QueueTask) -> None:
        """Write task result to JSONL log."""
        if not self._worker_cfg.log_jsonl or not self._log_path:
            return

        # Get provider route for observability
        provider_route = task.provider_route or str(getattr(self._llm, "model_route", ""))

        # Detect if test provider was used
        is_test_provider = any(
            provider_route.startswith(route)
            for route in ("stub://", "shadow-heuristic://", "test://")
        )

        entry = {
            "timestamp": utc_now(),
            "queue_task_id": task.id,
            "url": result.url,
            "source": result.source,
            "final_status": result.final_status.value,
            "failure_kind": result.failure_kind.value,
            "next_action": result.next_action.value,
            "output_path": result.output_path,
            "telegram_status": result.telegram_status,
            "prompt_bundle": result.prompt_bundle,
            "current_stage": result.current_stage,
            "score": result.score_result.score if result.score_result else None,
            "final_score": result.score_result.final_score if result.score_result else None,
            "signal_tier": result.score_result.signal_tier if result.score_result else None,
            "stage_count": len(result.stage_results),
            # Observability fields
            "runtime_mode": self._worker_cfg.mode.value,
            "provider_route": provider_route,
            "is_test_provider": is_test_provider,
            "runtime_fingerprint": task.runtime_fingerprint[:200] if task.runtime_fingerprint else "",
        }

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # Log failure should not stop worker

    # -- Signal handling -----------------------------------------------------

    def _handle_shutdown(self, signum: int, frame) -> None:  # type: ignore
        """Handle SIGINT/SIGTERM for graceful shutdown."""
        self._state.shutdown_requested = True

    def _wait(self, seconds: int) -> None:
        """Wait for specified seconds or until shutdown."""
        if seconds <= 0:
            return

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._state.shutdown_requested:
            remaining = deadline - time.monotonic()
            try:
                time.sleep(min(1.0, max(0.0, remaining)))
            except KeyboardInterrupt:
                self._state.shutdown_requested = True
                break


def _format_reply_status(task: QueueTask, result: ProcessResult) -> str:
    """Format a concise Telegram status update for manual submissions."""
    status_label = {
        QueueStatus.RETRY_SCHEDULED: "Retry scheduled",
        QueueStatus.REJECTED: "Rejected",
        QueueStatus.FAILED_TERMINAL: "Failed",
        QueueStatus.DONE: "Done",
    }.get(result.final_status, result.final_status.value)

    lines = [
        f"{status_label} (ID: {task.id})",
        task.url[:500],
        "",
        f"Status: {task.status.value}",
        f"Stage: {result.current_stage}",
    ]

    if task.result_title:
        lines.append(f"Title: {task.result_title[:200]}")
    if task.failure_kind.value:
        lines.append(f"Failure: {task.failure_kind.value}")
    if task.last_error:
        lines.append(f"Reason: {task.last_error[:500]}")
    if task.last_status_detail:
        lines.append(f"Detail: {task.last_status_detail[:500]}")
    if task.next_action.value:
        lines.append(f"Next action: {task.next_action.value}")
    if task.next_retry_at:
        lines.append(f"Retry at: {task.next_retry_at}")
    if task.output_path:
        lines.append(f"Output: {task.output_path}")

    lines.extend(["", f"Use /status {task.id} for the latest state."])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def create_worker(
    config: V3Config,
    *,
    queue_store: QueueStore | None = None,
    mode: RuntimeMode = RuntimeMode.STAGING,
) -> QueueWorker:
    """Create QueueWorker from V3Config."""
    if queue_store is None:
        from pathlib import Path
        loader = ConfigLoader(project_root=Path.cwd())
        queue_path = loader.expand_path(config.runtime.queue_db_path)
        queue_store = QueueStore(queue_path)

    log_path = None
    if config.runtime.log_path:
        log_path = Path(config.runtime.log_path).expanduser()

    worker_cfg = WorkerConfig(
        batch_size=config.worker.batch_size,
        max_consecutive_failures=config.live.max_consecutive_failures,
        processing_stale_after_minutes=30,
        log_jsonl=True,
        mode=mode,
    )

    return QueueWorker(
        config=config,
        queue_store=queue_store,
        worker_config=worker_cfg,
        log_path=log_path,
    )


def _create_default_fetcher(config: V3Config) -> Fetcher:
    agent_cfg = config.agent_reach
    if not agent_cfg.enabled:
        web_fetcher = WebPageFetcher()
        return FetcherRouter(agent_reach_fetcher=web_fetcher)

    # Pass wechat config to AgentReachFetcher
    from .fetchers.multi_channel import WechatConfig

    wechat_config = WechatConfig(
        headless_first=agent_cfg.wechat.headless_first,
        interactive_on_blocked=agent_cfg.wechat.interactive_on_blocked,
        profile_dir=agent_cfg.wechat.profile_dir,
        verification_timeout_seconds=agent_cfg.wechat.verification_timeout_seconds,
    ) if agent_cfg.wechat else None

    return FetcherRouter(
        agent_reach_fetcher=AgentReachFetcher(
            config_path=agent_cfg.config_path or None,
            enabled_channels=agent_cfg.enabled_channels or None,
            fallback_to_jina=agent_cfg.fallback_to_jina,
            proxy=agent_cfg.proxy or None,
            wechat_config=wechat_config,
        )
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for worker.

    Usage:
        python -m knowledge_extractor_v3.worker --once [--limit N]
        python -m knowledge_extractor_v3.worker --loop [--poll N] [--max-iter N]
    """
    import argparse

    parser = argparse.ArgumentParser(description="V3 Queue Worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one batch and exit",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run worker in continuous loop",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=10,
        help="Batch size limit (default: 10)",
    )
    parser.add_argument(
        "--poll",
        type=int,
        default=30,
        help="Poll interval in seconds for loop mode (default: 30)",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=0,
        help="Maximum iterations for loop mode (default: unlimited)",
    )
    parser.add_argument(
        "--mode", "-m",
        type=str,
        choices=["dry_run", "staging", "live", "auto"],
        default="auto",
        help="Runtime mode (default: auto = read from config)",
    )

    args = parser.parse_args(argv)

    if not args.once and not args.loop:
        parser.print_help()
        return 1

    # Load config
    project_root = Path(__file__).resolve().parents[2]
    loader = ConfigLoader(project_root=project_root)
    config = loader.load()

    # Resolve runtime paths using unified function
    paths = resolve_runtime_paths(project_root, config, loader, env=os.environ)

    # Runtime guard check
    guard = RuntimeGuard(paths)
    fingerprint = None
    try:
        fingerprint = guard.validate(write_fingerprint=False)
    except RuntimeGuardError as exc:
        print(f"Runtime guard check failed: {exc}", file=sys.stderr)
        return 1

    # Resolve mode: auto reads from config, explicit mode uses CLI value
    if args.mode == "auto":
        mode = RuntimeMode.LIVE if config.live.enabled else RuntimeMode.STAGING
    else:
        mode = RuntimeMode(args.mode)
    queue_store = QueueStore(
        paths.queue_db_path,
        runtime_fingerprint=fingerprint.to_dict() if fingerprint else None
    )

    worker_cfg = WorkerConfig(
        batch_size=args.limit,
        max_consecutive_failures=config.live.max_consecutive_failures,
        processing_stale_after_minutes=30,
        log_jsonl=True,
        mode=mode,
    )

    worker = QueueWorker(
        config=config,
        queue_store=queue_store,
        worker_config=worker_cfg,
        log_path=paths.log_path,
    )

    # Run
    try:
        if args.once:
            result = worker.run_once()
        else:
            result = worker.run_loop(poll_interval_seconds=args.poll, max_iterations=args.max_iter)
    except LiveModeUnavailable as exc:
        print(f"Live mode unavailable: {exc}", file=sys.stderr)
        return 1

    # Report
    print(f"Processed: {result.tasks_processed}")
    print(f"Succeeded: {result.tasks_succeeded}")
    print(f"Failed: {result.tasks_failed}")
    print(f"Consecutive failures: {result.consecutive_failures}")

    return 0 if result.consecutive_failures < worker_cfg.max_consecutive_failures else 1


if __name__ == "__main__":
    sys.exit(main())
