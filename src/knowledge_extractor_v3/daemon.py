"""V4 single daemon: source scanning and queue working in one process.

V3 ran four separate roles (scheduler, worker, telegram bot, health monitor).
V4 collapses the two processing roles into this loop: every ``--scan-interval``
seconds it discovers new items from the configured sources, and every
``--poll`` seconds it drains a batch of queue tasks through the absorption
pipeline. Delivery to WeChat stays Cindy-driven (schedule calls
``scripts/wechat_outbox.py``); the health monitor remains a control.sh role.

Usage:
    python -m knowledge_extractor_v3.daemon --loop [--poll 30] [--scan-interval 3600]
    python -m knowledge_extractor_v3.daemon --once
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from .config_loader import ConfigLoader, V3Config
from .models import RuntimeMode
from .queue_store import QueueStore
from .runtime_guard import RuntimeGuard, RuntimeGuardError, resolve_runtime_paths
from .scheduler import Scheduler
from .worker import QueueWorker, WorkerConfig


def _setup(project_root: Path) -> tuple[V3Config, QueueStore]:
    """Mirror the worker/scheduler main setup: config, paths, guard, queue."""
    loader = ConfigLoader(project_root=project_root)
    config = loader.load()
    paths = resolve_runtime_paths(project_root, config, loader, env=os.environ)
    guard = RuntimeGuard(paths)
    guard.validate(write_fingerprint=False)  # raises RuntimeGuardError
    return config, QueueStore(paths.queue_db_path)


def run_once(*, scan: bool = True, batch_size: int = 10) -> int:
    """One scan pass (optional) plus one worker batch. Returns exit code."""
    project_root = Path.cwd()
    config, queue_store = _setup(project_root)

    if scan:
        scheduler = Scheduler(config, queue_store=queue_store)
        scheduler.run_once()

    mode = RuntimeMode.LIVE if config.live.enabled else RuntimeMode.STAGING
    worker_cfg = WorkerConfig(
        batch_size=batch_size,
        max_consecutive_failures=config.live.max_consecutive_failures,
        processing_stale_after_minutes=30,
        log_jsonl=True,
        mode=mode,
    )
    worker = QueueWorker(config, queue_store=queue_store, worker_config=worker_cfg)
    result = worker.run_once()
    return 0 if result.tasks_failed == 0 else 1


def _run_periodic_dedup() -> float:
    """One vault-dedup pass; returns the seconds until the next pass."""
    interval_hours = 24
    try:
        loader = ConfigLoader(project_root=Path.cwd())
        config = loader.load()
        dedup = config.dedup
        interval_hours = max(1, int(dedup.dedup_interval_hours))
        if dedup.enabled and config.outputs.obsidian_root:
            from .outputs.dedupe import dedupe_vault

            root = loader.expand_path(config.outputs.obsidian_root)
            report = dedupe_vault(root)
            if report.merged_groups or report.restored or report.errors:
                print(
                    f"[daemon] vault dedup: {report.merged_groups} groups merged, "
                    f"{len(report.restored)} restored, {len(report.errors)} errors",
                    file=sys.stderr, flush=True,
                )
    except Exception as exc:  # dedup must never kill the daemon loop
        print(f"[daemon] vault dedup failed: {exc}", file=sys.stderr, flush=True)
    return interval_hours * 3600.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V4 single daemon (scan + work)")
    parser.add_argument("--loop", action="store_true", help="Run forever (default: one pass)")
    parser.add_argument("--poll", type=int, default=30, help="Seconds between worker batches")
    parser.add_argument(
        "--scan-interval",
        type=int,
        default=3600,
        help="Seconds between source scans",
    )
    parser.add_argument("--limit", type=int, default=10, help="Worker batch size")
    parser.add_argument("--no-scan", action="store_true", help="Worker only (manual scans)")
    parser.add_argument("--no-dedup", action="store_true", help="Disable periodic vault dedupe")
    parser.add_argument("--max-iter", type=int, default=0, help="Stop after N loops (0 = forever)")
    args = parser.parse_args(argv)

    if not args.loop:
        try:
            return run_once(scan=not args.no_scan, batch_size=args.limit)
        except RuntimeGuardError as exc:
            print(f"Runtime guard check failed: {exc}", file=sys.stderr)
            return 1

    next_scan_at = 0.0  # scan immediately on start
    # Startup grace: never dedupe in the first minutes — loop tests load the
    # real config, and an immediate trigger would touch the real vault.
    next_dedup_at = time.time() + 300
    iterations = 0
    magazine_server = None
    while True:
        try:
            # Bring the magazine service up before the first scan so a stalled
            # source fetch cannot delay localhost availability.
            if magazine_server is None:
                config = ConfigLoader(project_root=Path.cwd()).load()
                if config.outputs.magazine_enabled and config.outputs.obsidian_root:
                    from .magazine import MagazineServer, build_issue, build_reviewer

                    loader = ConfigLoader(project_root=Path.cwd())
                    root = loader.expand_path(config.outputs.obsidian_root)
                    build_issue(root)
                    try:
                        magazine_server = MagazineServer(
                            root,
                            port=config.outputs.magazine_port,
                            reviewer=build_reviewer(config, root),
                        )
                        magazine_server.start()
                    except OSError as exc:
                        print(f"[daemon] magazine service unavailable: {exc}", file=sys.stderr, flush=True)
                        magazine_server = False
                else:
                    magazine_server = False
            scan = not args.no_scan and time.time() >= next_scan_at
            code = run_once(scan=scan, batch_size=args.limit)
            if scan:
                next_scan_at = time.time() + args.scan_interval
            if code != 0:
                print(f"[daemon] worker batch reported failures (exit={code})", flush=True)
            if not args.no_dedup and time.time() >= next_dedup_at:
                next_dedup_at = time.time() + _run_periodic_dedup()
        except KeyboardInterrupt:
            if magazine_server not in (None, False):
                magazine_server.close()
            print("[daemon] interrupted, exiting", flush=True)
            return 0
        except Exception as exc:  # keep the daemon alive; control.sh restarts are slow
            print(f"[daemon] loop error: {exc}", file=sys.stderr, flush=True)

        iterations += 1
        if args.max_iter and iterations >= args.max_iter:
            return 0
        time.sleep(max(1, args.poll))


if __name__ == "__main__":
    raise SystemExit(main())
