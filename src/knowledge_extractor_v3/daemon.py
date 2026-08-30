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
import fcntl
import os
import signal
import sys
import time
from pathlib import Path

from .config_loader import ConfigLoader, V3Config
from .models import RuntimeMode
from .queue_store import QueueStore
from .runtime_guard import RuntimeGuard, RuntimeGuardError, resolve_runtime_paths
from .scheduler import Scheduler
from .worker import QueueWorker, WorkerConfig

_shutdown_requested = False


class SingletonContended(RuntimeError):
    """Another loop daemon already holds the singleton lock."""


def _handle_shutdown(signum: int, frame) -> None:  # type: ignore
    global _shutdown_requested
    _shutdown_requested = True


def _acquire_singleton_lock() -> int | None:
    """Best-effort exclusive flock so one loop daemon runs per queue DB.

    The lock lives in its own sidecar file next to the database — NEVER on
    the database itself: on macOS an flock on the db file collides with
    SQLite's fcntl byte-range locks and deadlocks the daemon's own queries
    ("database is locked" in production, 2026-08-30). Returns the fd to hold
    for the process lifetime. Raises SingletonContended when another daemon
    already holds it; returns None when the lock cannot even be attempted —
    the queue CAS still protects tasks then.
    """
    try:
        loader = ConfigLoader(project_root=Path.cwd())
        config = loader.load()
        db_path = loader.expand_path(config.runtime.queue_db_path)
        lock_path = db_path.parent / (db_path.name + ".loop.lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except Exception:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise SingletonContended(str(lock_path))
    return fd


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
        # The loop owns signal handling: the worker must not overwrite the
        # daemon's SIGTERM handler every batch.
        manage_signals=False,
        shutdown_check=lambda: _shutdown_requested,
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

    global _shutdown_requested
    _shutdown_requested = False
    signal.signal(signal.SIGTERM, _handle_shutdown)

    try:
        lock_fd = _acquire_singleton_lock()
    except SingletonContended as exc:
        print(f"[daemon] another loop daemon already holds {exc}; exiting", file=sys.stderr, flush=True)
        return 0

    next_scan_at = 0.0  # scan immediately on start
    # Startup grace: never dedupe in the first minutes — loop tests load the
    # real config, and an immediate trigger would touch the real vault.
    next_dedup_at = time.time() + 300
    iterations = 0
    magazine_server = None
    magazine_retry_at = 0.0  # bind failures back off instead of disabling for life
    try:
        while True:
            try:
                # Bring the magazine service up before the first scan so a
                # stalled source fetch cannot delay localhost availability. A
                # bind failure (port held by a stale process) retries on its
                # own schedule instead of disabling the service for life.
                if magazine_server is None and time.time() >= magazine_retry_at:
                    config = ConfigLoader(project_root=Path.cwd()).load()
                    if config.outputs.magazine_enabled and config.outputs.obsidian_root:
                        from .magazine import MagazineServer, build_issue, build_reviewer

                        loader = ConfigLoader(project_root=Path.cwd())
                        root = loader.expand_path(config.outputs.obsidian_root)
                        build_issue(root)
                        try:
                            server = MagazineServer(
                                root,
                                port=config.outputs.magazine_port,
                                reviewer=build_reviewer(config, root),
                            )
                            server.start()
                            magazine_server = server
                        except OSError as exc:
                            print(
                                f"[daemon] magazine service unavailable: {exc}; retrying in 300s",
                                file=sys.stderr, flush=True,
                            )
                            magazine_retry_at = time.time() + 300
                    else:
                        magazine_server = False
                scan = not args.no_scan and time.time() >= next_scan_at
                code = run_once(scan=scan, batch_size=args.limit)
                # The worker no longer installs its own handlers inside the
                # loop, but re-assert ours in case anything overwrote them.
                signal.signal(signal.SIGTERM, _handle_shutdown)
                if scan:
                    next_scan_at = time.time() + args.scan_interval
                if code != 0:
                    print(f"[daemon] worker batch reported failures (exit={code})", flush=True)
                if not _shutdown_requested and not args.no_dedup and time.time() >= next_dedup_at:
                    next_dedup_at = time.time() + _run_periodic_dedup()
            except KeyboardInterrupt:
                print("[daemon] interrupted, exiting", flush=True)
                return 0
            except Exception as exc:  # keep the daemon alive; control.sh restarts are slow
                print(f"[daemon] loop error: {exc}", file=sys.stderr, flush=True)

            if _shutdown_requested:
                print("[daemon] shutdown requested, exiting", flush=True)
                return 0
            iterations += 1
            if args.max_iter and iterations >= args.max_iter:
                return 0
            try:
                for _ in range(max(1, args.poll)):
                    if _shutdown_requested:
                        break
                    time.sleep(1)
            except KeyboardInterrupt:
                return 0
    finally:
        if magazine_server not in (None, False):
            magazine_server.close()
        if lock_fd is not None:
            os.close(lock_fd)


if __name__ == "__main__":
    raise SystemExit(main())
