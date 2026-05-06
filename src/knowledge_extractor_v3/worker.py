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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config_loader import ConfigLoader, V3Config
from .fetchers.base import Fetcher
from .fetchers.multi_channel import AgentReachFetcher
from .fetchers.router import FetcherRouter
from .fetchers.web import WebPageFetcher
from .live_gate import LiveGate
from .llm.provider import LLMProvider, StubLLMProvider
from .models import ProcessResult, QueueStatus, RuntimeMode, utc_now
from .pipeline import Pipeline
from .prompt_registry import PromptRegistry
from .queue_store import QueueStore, QueueTask
from .runtime_guard import RuntimeGuard, RuntimeGuardError


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
    ) -> None:
        self._config = config
        self._queue = queue_store
        self._fetcher = fetcher or _create_default_fetcher(config)
        self._llm = llm_provider or StubLLMProvider()
        self._prompts = prompt_registry or PromptRegistry.default(Path.cwd())
        self._worker_cfg = worker_config or WorkerConfig()
        self._log_path = log_path

        # Setup signal handlers for graceful shutdown
        self._state = WorkerState()
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # -- Main entry points ---------------------------------------------------

    def run_once(self) -> WorkerRunResult:
        """Run a single batch of tasks and return results."""
        # Recover stale processing tasks
        recovered = self._recover_stale_tasks()

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

        return WorkerRunResult(
            tasks_processed=self._state.total_processed,
            tasks_succeeded=self._state.total_succeeded,
            tasks_failed=self._state.total_failed,
            tasks_recovered=recovered,
            consecutive_failures=self._state.consecutive_failures,
            should_stop=(
                self._state.shutdown_requested
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
        mode = self._determine_runtime_mode()

        # Build pipeline with appropriate output port
        pipeline = Pipeline(
            queue_store=self._queue,
            fetcher=self._fetcher,
            llm_provider=self._llm,
            prompt_registry=self._prompts,
            staging_root=self._queue.db_path.parent / "staging",
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
                token = loader.resolve_env(self._config.outputs.telegram_bot_token_env)
                chat_id = loader.resolve_env(self._config.outputs.telegram_admin_chat_id_env)
                if token and chat_id:
                    telegram = LiveTelegramClient(
                        bot_token=token,
                        chat_id=chat_id,
                        enabled=True,
                    )

            live_output = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)

            # Replace the pipeline's live output
            pipeline._live_output = live_output

            # Use live provider
            self._llm = create_live_provider(self._config.llm, env=os.environ)
            pipeline.llm_provider = self._llm

        return pipeline.process_url(
            task.url,
            source=task.source,
            queue_task_id=task.id,
            mode=mode,
        )

    def _determine_runtime_mode(self) -> RuntimeMode:
        """Determine runtime mode based on config and live gate."""
        if self._worker_cfg.mode is not RuntimeMode.LIVE:
            return self._worker_cfg.mode

        # If live is not enabled, use staging
        if not self._config.live.enabled:
            return RuntimeMode.STAGING

        # Check live gate
        loader = ConfigLoader(project_root=Path.cwd())
        guard = RuntimeGuard.from_env(project_root=Path.cwd())
        gate = LiveGate(self._config, config_loader=loader, runtime_guard=guard)

        result = gate.check()
        if result.passed:
            return RuntimeMode.LIVE
        return RuntimeMode.STAGING

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

        # Check for shutdown every second
        for _ in range(seconds):
            if self._state.shutdown_requested:
                break
            try:
                signal.pause()
            except KeyboardInterrupt:
                self._state.shutdown_requested = True
                break


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

    return FetcherRouter(
        agent_reach_fetcher=AgentReachFetcher(
            config_path=agent_cfg.config_path or None,
            enabled_channels=agent_cfg.enabled_channels or None,
            fallback_to_jina=agent_cfg.fallback_to_jina,
            proxy=agent_cfg.proxy or None,
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
        choices=["dry_run", "staging", "live"],
        default="staging",
        help="Runtime mode (default: staging)",
    )

    args = parser.parse_args(argv)

    if not args.once and not args.loop:
        parser.print_help()
        return 1

    # Load config
    project_root = Path(__file__).resolve().parents[2]
    loader = ConfigLoader(project_root=project_root)
    config = loader.load()

    # Runtime guard check
    guard = RuntimeGuard.from_env(project_root=project_root)
    fingerprint = None
    try:
        fingerprint = guard.validate(write_fingerprint=False)
    except RuntimeGuardError as exc:
        print(f"Runtime guard check failed: {exc}", file=sys.stderr)
        return 1

    # Create worker
    mode = RuntimeMode(args.mode)
    queue_path = loader.expand_path(config.runtime.queue_db_path)
    queue_store = QueueStore(queue_path, runtime_fingerprint=fingerprint.to_dict() if fingerprint else None)

    log_path = Path(config.runtime.log_path).expanduser() if config.runtime.log_path else None
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
        log_path=log_path,
    )

    # Run
    if args.once:
        result = worker.run_once()
    else:
        result = worker.run_loop(poll_interval_seconds=args.poll, max_iterations=args.max_iter)

    # Report
    print(f"Processed: {result.tasks_processed}")
    print(f"Succeeded: {result.tasks_succeeded}")
    print(f"Failed: {result.tasks_failed}")
    print(f"Consecutive failures: {result.consecutive_failures}")

    return 0 if result.consecutive_failures < worker_cfg.max_consecutive_failures else 1


if __name__ == "__main__":
    sys.exit(main())
