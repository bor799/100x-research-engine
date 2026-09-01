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
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from .config_loader import ConfigLoader, V3Config
from .fetchers.base import Fetcher
from .fetchers.multi_channel import AgentReachFetcher
from .fetchers.router import FetcherRouter
from .fetchers.web import WebPageFetcher
from .live_gate import LiveGate
from .llm.provider import LLMProvider, StubLLMProvider
from .models import FetchedContent, ProcessResult, QueueStatus, RuntimeMode, TypedError, retry_at, utc_now
from .pipeline import Pipeline
from .queue_store import FailureKind, NextAction, QueueClaimConflict, QueueStore, QueueTask
from .runtime_guard import RuntimeGuard, RuntimeGuardError, RuntimePaths, resolve_runtime_paths


class LiveModeUnavailable(RuntimeError):
    """Raised when LIVE was requested but the live gate does not pass."""


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
    heartbeat_interval_seconds: float = 20.0
    log_jsonl: bool = True
    mode: RuntimeMode = RuntimeMode.STAGING
    # When the daemon drives the worker it owns signal handling itself, so the
    # worker must not overwrite the daemon's handlers; the daemon instead polls
    # shutdown_check between tasks to cut a batch short.
    manage_signals: bool = True
    shutdown_check: Callable[[], bool] | None = None


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
        worker_config: WorkerConfig | None = None,
        log_path: Path | None = None,
    ) -> None:
        self._config = config
        self._queue = queue_store
        self._fetcher = fetcher or _create_default_fetcher(config)
        self._llm = llm_provider or StubLLMProvider()
        self._worker_cfg = worker_config or WorkerConfig()
        self._log_path = log_path

        # Setup signal handlers for graceful shutdown (unless the embedding
        # daemon manages signals itself and passes a shutdown_check instead).
        self._state = WorkerState()
        if self._worker_cfg.manage_signals:
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

            if self._worker_cfg.shutdown_check is not None and self._worker_cfg.shutdown_check():
                break

            if self._state.consecutive_failures >= self._worker_cfg.max_consecutive_failures:
                break

            result = self._process_task(task)
            if result is None:
                continue  # claim race lost — another worker owns this task
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

    def _process_task(self, task: QueueTask) -> ProcessResult | None:
        """Process a single task through the pipeline.

        Returns ``None`` when the atomic claim loses the race — the task is
        another worker's, so it is neither a success nor a failure here.
        """
        import socket

        # Determine mode first (may raise LiveModeUnavailable)
        # Do NOT claim the task yet - Live gate check happens before claim
        mode = self._determine_runtime_mode()

        # Eagerly swap to live provider before claiming so provider_route is correct.
        if mode is RuntimeMode.LIVE and not str(getattr(self._llm, "model_route", "")).startswith("live://"):
            from .llm.live_provider import create_live_provider
            self._llm = create_live_provider(self._config.llm, env=os.environ)

        # Now that we know the mode is valid, claim the task. The owner is
        # unique per claim so a rebooted pid can never collide with a lease
        # written before the reboot.
        owner = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        provider_route = str(getattr(self._llm, "model_route", ""))
        try:
            task = self._queue.mark_processing(task.id, owner=owner, provider_route=provider_route)
        except QueueClaimConflict as exc:
            print(f"[worker] {exc}", file=sys.stderr, flush=True)
            return None

        # A background heartbeat keeps the lease fresh while the pipeline
        # fetches and absorbs, so the 30-minute stale recovery can only ever
        # reclaim tasks of genuinely dead workers.
        stop_heartbeat = threading.Event()
        heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(task.id, owner, stop_heartbeat),
            daemon=True,
        )
        heartbeat_thread.start()
        try:
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
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=max(2.0, self._worker_cfg.heartbeat_interval_seconds))

    def _heartbeat_loop(self, task_id: int, owner: str, stop: threading.Event) -> None:
        """Refresh the claimed task's lease until told to stop."""
        interval = max(1.0, float(self._worker_cfg.heartbeat_interval_seconds))
        while not stop.wait(interval):
            try:
                self._queue.update_heartbeat(task_id, owner=owner)
            except Exception:
                pass  # best effort; stale recovery covers the gaps

    def _process_task_impl(self, task: QueueTask, owner: str, mode: RuntimeMode) -> ProcessResult:
        """Inner processing logic for a single task."""
        # LIVE-only vault dedup: same index feeds the pipeline early exit and
        # the writer guard, so one task sees one consistent vault view.
        vault_dedup = None
        story_dedup = None
        if mode is RuntimeMode.LIVE and self._config.dedup.enabled:
            from .config_loader import ConfigLoader
            from .outputs.vault_index import VaultDedupService, VaultIndex

            live_loader = ConfigLoader(project_root=Path.cwd())
            live_root = live_loader.expand_path(self._config.outputs.obsidian_root)
            vault_index = VaultIndex(live_root)
            vault_index.rebuild()
            vault_dedup = VaultDedupService(
                vault_index,
                complete_fn=self._llm.complete,
                similarity_threshold=self._config.dedup.update_similarity_threshold,
            )
            # Story-identity dedup: cross-transport duplicates (aggregator
            # digests of an already-archived article) complete at the
            # canonical file instead of forking a second article.
            if self._config.dedup.story_dedup:
                from .outputs.story_identity import StoryDedupService

                story_dedup = StoryDedupService(
                    live_root,
                    rare_min=self._config.dedup.story_rare_tokens,
                    mass_min=self._config.dedup.story_mass_tokens,
                    strong_min=self._config.dedup.story_strong_tokens,
                    overlap_min=self._config.dedup.story_overlap_min,
                    strong_jaccard=self._config.dedup.story_strong_jaccard,
                    title_min=self._config.dedup.story_title_min,
                    window_weeks=self._config.dedup.story_window_weeks,
                    max_df=self._config.dedup.story_max_df,
                )

        # Build pipeline with appropriate output port
        pipeline = Pipeline(
            queue_store=self._queue,
            fetcher=self._fetcher,
            llm_provider=self._llm,
            staging_root=self._queue.db_path.parent / "staging",
            source_preferences=self._config.routing.source_preferences,
            live_output=None,  # Will be set by mode if needed
            vault_dedup=vault_dedup,
            story_dedup=story_dedup,
        )

        # For live mode, the pipeline needs a live output port
        # This is handled by Pipeline._output_port() based on mode
        if mode is RuntimeMode.LIVE:
            from .llm.live_provider import create_live_provider
            from .outputs.live_obsidian import LiveOutputPort, LiveObsidianWriter
            from .outputs.wechat_queue import WechatQueue
            from .config_loader import ConfigLoader

            loader = ConfigLoader(project_root=Path.cwd())
            obsidian_root = loader.expand_path(self._config.outputs.obsidian_root)

            writer = LiveObsidianWriter(
                root=obsidian_root,
                subdir=self._config.outputs.obsidian_subdir,
                write_manifest=self._config.outputs.write_manifest,
                vault_index=vault_dedup.index if vault_dedup is not None else None,
                dedup_guard=self._config.dedup.enabled,
            )

            wechat_queue = None
            channel = self._config.outputs.channel
            if channel in {"wechat", "both"}:
                queue_dir = loader.expand_path(self._config.outputs.wechat_queue_dir)
                wechat_queue = WechatQueue(queue_dir)

            live_output = LiveOutputPort(
                obsidian_writer=writer,
                wechat_queue=wechat_queue,
                enqueue_individual_cards=self._config.outputs.enqueue_individual_cards,
            )

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
            "wechat_status": result.wechat_status,
            "prompt_bundle": result.prompt_bundle,
            "current_stage": result.current_stage,
            "score": result.score_result.score if result.score_result else None,
            "final_score": result.score_result.final_score if result.score_result else None,
            "signal_tier": result.score_result.signal_tier if result.score_result else None,
            "information_gain": result.score_result.information_gain if result.score_result else None,
            "action_value": result.score_result.action_value if result.score_result else None,
            "relevance": result.score_result.relevance if result.score_result else None,
            "is_spam": result.score_result.is_spam if result.score_result else None,
            "route": result.route,
            "brief_contract_failed": result.brief_contract_failed,
            "dedup_outcome": result.dedup_outcome,
            "stage_count": len(result.stage_results),
            # Per-stage timing for cost/latency accounting.
            "stage_timings_ms": {
                s.stage: s.duration_ms for s in result.stage_results
            },
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
