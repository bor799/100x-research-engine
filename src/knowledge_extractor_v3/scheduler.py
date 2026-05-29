"""Scheduler for periodic source discovery and queue ingestion.

The scheduler:
- Loads source registry
- Discovers items from enabled sources
- Enqueues new URLs (does not process them)
- Applies per-source and per-tick limits
- Writes JSONL event log
- Supports --once and --loop modes
"""

from __future__ import annotations

import json
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .config_loader import ConfigLoader, V3Config
from .models import utc_now
from .queue_store import QueueStore, QueueTask
from .runtime_guard import RuntimeGuard, RuntimeGuardError, resolve_runtime_paths
from .sources import (
    RSSAdapter,
    SchedulerEvent,
    SourceConfig,
    SourceItem,
    SourceRegistry,
    URLListAdapter,
    URLDeduper,
)

if TYPE_CHECKING:
    from .config_loader import SchedulerConfig as V3SchedulerConfig


@dataclass
class SchedulerRunResult:
    """Result of a single scheduler tick."""

    sources_processed: int
    items_discovered: int
    items_enqueued: int
    items_skipped_duplicate: int
    items_skipped_limit: int
    events: list[SchedulerEvent]
    should_stop: bool = False


class Scheduler:
    """V3 scheduler for periodic source discovery and queue ingestion.

    Scheduler only enqueues URLs; it does not process them through the pipeline.
    Processing is done by the worker.
    """

    def __init__(
        self,
        config: V3Config,
        *,
        queue_store: QueueStore,
        source_registry: SourceRegistry | None = None,
        log_path: Path | None = None,
    ) -> None:
        self._config = config
        self._queue = queue_store
        self._registry = source_registry or self._create_registry()
        self._log_path = log_path
        self._deduper = URLDeduper()

        # Setup signal handlers
        self._shutdown_requested = False
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # -- Main entry points ---------------------------------------------------

    def run_once(
        self,
        *,
        max_total_items: int = 20,
        lookback_days: int = 7,
    ) -> SchedulerRunResult:
        """Run a single scheduler tick.

        Discovers items from all enabled sources and enqueues new URLs.
        """
        events: list[SchedulerEvent] = []

        # Load existing URLs into deduper
        self._prime_deduper()

        # Discover items from all sources
        all_items = self._discover_all_items(lookback_days, events)

        # Filter and enqueue
        enqueued = 0
        skipped_duplicate = 0
        skipped_limit = 0

        # Sort by priority (lower first)
        all_items.sort(key=lambda i: i.priority)

        for item in all_items:
            if self._shutdown_requested:
                break

            if enqueued >= max_total_items:
                skipped_limit += len(all_items) - enqueued
                break

            # Check for duplicates
            if self._deduper.is_seen(item.url):
                skipped_duplicate += 1
                events.append(SchedulerEvent(
                    timestamp=utc_now(),
                    source_id=item.source_id,
                    event_type="skipped",
                    message="Duplicate URL",
                    detail={"url": item.url},
                ))
                continue

            # Enqueue
            task = self._enqueue_item(item)
            if task:
                enqueued += 1
                self._deduper.mark_seen(item.url)

                events.append(SchedulerEvent(
                    timestamp=utc_now(),
                    source_id=item.source_id,
                    event_type="discovered",
                    count=1,
                    message=f"Enqueued: {item.title[:50]}",
                    detail={"url": item.url, "task_id": task.id},
                ))

        # Write log
        self._write_events(events)

        return SchedulerRunResult(
            sources_processed=len(self._registry.all_enabled_sources()),
            items_discovered=len(all_items),
            items_enqueued=enqueued,
            items_skipped_duplicate=skipped_duplicate,
            items_skipped_limit=skipped_limit,
            events=events,
            should_stop=self._shutdown_requested,
        )

    def run_loop(
        self,
        *,
        interval_seconds: int = 300,
        max_iterations: int = 0,
    ) -> SchedulerRunResult:
        """Run scheduler in a loop.

        Args:
            interval_seconds: Seconds between ticks.
            max_iterations: Maximum iterations (0 = unlimited).
        """
        iteration = 0
        totals = SchedulerRunResult(
            sources_processed=0,
            items_discovered=0,
            items_enqueued=0,
            items_skipped_duplicate=0,
            items_skipped_limit=0,
            events=[],
            should_stop=False,
        )

        while not self._shutdown_requested:
            if max_iterations > 0 and iteration >= max_iterations:
                break

            result = self.run_once()

            # Accumulate totals
            totals = SchedulerRunResult(
                sources_processed=totals.sources_processed + result.sources_processed,
                items_discovered=totals.items_discovered + result.items_discovered,
                items_enqueued=totals.items_enqueued + result.items_enqueued,
                items_skipped_duplicate=totals.items_skipped_duplicate + result.items_skipped_duplicate,
                items_skipped_limit=totals.items_skipped_limit + result.items_skipped_limit,
                events=totals.events + result.events,
                should_stop=result.should_stop,
            )

            iteration += 1

            if result.should_stop:
                break

            if result.items_enqueued == 0:
                # No items, wait before next tick
                self._wait(interval_seconds)

        return totals

    # -- Internal methods ----------------------------------------------------

    def _create_registry(self) -> SourceRegistry:
        """Create source registry from config."""
        project_root = Path(__file__).resolve().parents[2]

        # Convert V3SourceConfig (with 'name') to SourceConfig (with 'id')
        from .sources.models import SourceConfig as SSourceConfig
        converted_sources = []
        for src in self._config.sources:
            # Extract additional fields that might be in config
            metadata = dict(getattr(src, "metadata", {}) or {})
            if getattr(src, "category", ""):
                metadata.setdefault("category", src.category)
            if getattr(src, "cron_interval", ""):
                metadata.setdefault("cron_interval", src.cron_interval)
            src_dict = {
                "id": src.name,  # V3SourceConfig.name -> SourceConfig.id
                "type": src.type,
                "url": src.url,
                "enabled": src.enabled,
                "priority": getattr(src, "priority", 100),
                "tags": getattr(src, "tags", []),
                "path": getattr(src, "path", ""),
                "max_items": getattr(src, "max_items", 10),
                "lookback_days": getattr(src, "lookback_days", 7),
                "metadata": metadata,
            }
            converted_sources.append(SSourceConfig(**src_dict))

        registry = SourceRegistry(
            sources=converted_sources,
            project_root=project_root,
        )

        # Register built-in adapters
        registry.register_adapter(RSSAdapter())
        registry.register_adapter(URLListAdapter())

        return registry

    def _prime_deduper(self) -> None:
        """Load existing queue URLs into deduper."""
        # Get all pending/retry_scheduled tasks
        # For efficiency, we'll just use the find_by_url check during enqueue
        pass

    def _discover_all_items(
        self,
        lookback_days: int,
        events: list[SchedulerEvent],
    ) -> list[SourceItem]:
        """Discover items from all enabled sources."""
        all_items: list[SourceItem] = []

        for source in self._registry.all_enabled_sources():
            source_lookback = getattr(source, "lookback_days", lookback_days) or lookback_days
            adapter = self._registry.get_adapter(source.type)
            items = self._registry.discover_items(
                source,
                lookback_days=source_lookback,
            )
            adapter_error = getattr(adapter, "last_error", None) if adapter is not None else None

            # Apply per-source limit from config
            max_items = getattr(source, "max_items", 10)
            items = items[:max_items]

            # Apply source priority
            items = [i.with_priority(source.priority) for i in items]

            all_items.extend(items)

            if adapter_error is not None:
                events.append(SchedulerEvent(
                    timestamp=utc_now(),
                    source_id=source.id,
                    event_type="error",
                    count=0,
                    message=adapter_error.message,
                    detail={
                        "failure_kind": adapter_error.failure_kind.value,
                        "next_action": adapter_error.next_action.value,
                        "detail": adapter_error.detail,
                    },
                ))
            else:
                events.append(SchedulerEvent(
                    timestamp=utc_now(),
                    source_id=source.id,
                    event_type="discovered",
                    count=len(items),
                    message=f"Discovered {len(items)} items",
                ))

        return all_items

    def _enqueue_item(self, item: SourceItem) -> QueueTask | None:
        """Enqueue a single source item."""
        try:
            # Check if already in queue
            existing = self._queue.find_by_url(item.url)
            if existing is not None:
                return None

            # Enqueue new task
            task = self._queue.enqueue(
                item.url,
                source=item.source_id,
                priority=item.priority,
                reply_channel=item.reply_channel,
                reply_chat_id=item.reply_chat_id,
            )
            return task
        except Exception:
            return None

    def _write_events(self, events: list[SchedulerEvent]) -> None:
        """Write events to JSONL log."""
        if not self._log_path or not events:
            return

        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._log_path.open("a", encoding="utf-8") as f:
                for event in events:
                    entry = {
                        "timestamp": event.timestamp,
                        "source_id": event.source_id,
                        "event_type": event.event_type,
                        "count": event.count,
                        "message": event.message,
                        "detail": event.detail,
                    }
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _handle_shutdown(self, signum: int, frame) -> None:  # type: ignore
        """Handle shutdown signals."""
        self._shutdown_requested = True

    def _wait(self, seconds: int) -> None:
        """Wait for signal or timeout."""
        if seconds <= 0:
            return

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._shutdown_requested:
            remaining = deadline - time.monotonic()
            try:
                time.sleep(min(1.0, max(0.0, remaining)))
            except KeyboardInterrupt:
                self._shutdown_requested = True
                break


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def create_scheduler(
    config: V3Config,
    *,
    queue_store: QueueStore | None = None,
) -> Scheduler:
    """Create Scheduler from V3Config."""
    if queue_store is None:
        from pathlib import Path
        loader = ConfigLoader(project_root=Path.cwd())
        queue_path = loader.expand_path(config.runtime.queue_db_path)
        queue_store = QueueStore(queue_path)

    log_path = None
    if config.runtime.log_path:
        log_path = Path(config.runtime.log_path).expanduser()
        # Replace .log with -scheduler.jsonl
        # with_suffix only replaces the last suffix, so we need string manipulation
        if log_path.suffix == ".log":
            log_path = log_path.with_name(log_path.stem + "-scheduler.jsonl")
        else:
            log_path = log_path.with_name(log_path.name + "-scheduler.jsonl")

    return Scheduler(
        config=config,
        queue_store=queue_store,
        log_path=log_path,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for scheduler.

    Usage:
        python -m knowledge_extractor_v3.scheduler --once [--limit N]
        python -m knowledge_extractor_v3.scheduler --loop [--interval N] [--max-iter N]
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(description="V3 Scheduler")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one tick and exit",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Run scheduler in continuous loop",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=20,
        help="Max items per tick (default: 20)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=7,
        help="Lookback days for RSS feeds (default: 7)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Poll interval in seconds for loop mode (default: 300)",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=0,
        help="Maximum iterations for loop mode (default: unlimited)",
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
    try:
        guard.validate(write_fingerprint=False)
    except RuntimeGuardError as exc:
        print(f"Runtime guard check failed: {exc}", file=sys.stderr)
        return 1

    # Create scheduler with unified paths
    queue_store = QueueStore(paths.queue_db_path)
    scheduler = Scheduler(
        config=config,
        queue_store=queue_store,
        log_path=paths.log_path,
    )

    # Run
    if args.once:
        result = scheduler.run_once(max_total_items=args.limit, lookback_days=args.lookback)
    else:
        result = scheduler.run_loop(
            interval_seconds=args.interval,
            max_iterations=args.max_iter,
        )

    # Report
    print(f"Sources processed: {result.sources_processed}")
    print(f"Items discovered: {result.items_discovered}")
    print(f"Items enqueued: {result.items_enqueued}")
    print(f"Skipped (duplicate): {result.items_skipped_duplicate}")
    print(f"Skipped (limit): {result.items_skipped_limit}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
