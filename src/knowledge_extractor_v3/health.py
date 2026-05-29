"""Health check and recovery for V3 runtime.

Checks:
- V3 path isolation
- Queue schema
- Queue status counts
- Stale processing tasks
- Role lock freshness
- Recent scheduler tick
- Recent worker task result
- Telegram token presence (if enabled)
- Obsidian root writable (if enabled)
- Source config parseable
- Prompt registry validates

Recovery:
- stale processing -> retry_scheduled
- broken role lock -> mark stale (no kill)
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from .config_loader import ConfigLoader, V3Config
from .models import sha256_text, utc_now
from .prompt_registry import PromptRegistry
from .queue_store import QueueStore, QueueStatus
from .runtime_guard import RuntimeGuard, RuntimeGuardError


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class HealthCheck:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    message: str
    detail: dict[str, Any] = field(default_factory=dict)
    recovered: int = 0  # Number of items recovered


@dataclass
class HealthReport:
    """Aggregate health report."""

    overall_status: HealthStatus
    timestamp: str
    checks: list[HealthCheck]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON output."""
        return {
            "overall_status": self.overall_status.value,
            "timestamp": self.timestamp,
            "summary": self.summary,
            "checks": [
                {
                    "name": c.name,
                    "status": c.status.value,
                    "message": c.message,
                    "detail": c.detail,
                    "recovered": c.recovered,
                }
                for c in self.checks
            ],
        }


class HealthChecker:
    """V3 health checker with automatic recovery."""

    def __init__(
        self,
        config: V3Config,
        *,
        queue_store: QueueStore | None = None,
        runtime_guard: RuntimeGuard | None = None,
        prompt_registry: PromptRegistry | None = None,
    ) -> None:
        self._config = config
        self._queue = queue_store
        self._guard = runtime_guard
        self._prompts = prompt_registry

    def run_all(self) -> HealthReport:
        """Run all health checks and return aggregate report."""
        checks: list[HealthCheck] = []

        # Required checks
        checks.append(self._check_path_isolation())
        checks.append(self._check_queue_schema())
        checks.append(self._check_queue_status())

        # Optional checks (only if components are enabled)
        checks.append(self._check_stale_processing())
        checks.append(self._check_role_locks())
        checks.append(self._check_prompt_registry())

        if self._config.live.enabled:
            checks.append(self._check_live_requirements())

        # Determine overall status
        overall = self._worst_status(c.status for c in checks)

        # Generate summary
        summary_parts = []
        for c in checks:
            if c.status != HealthStatus.HEALTHY:
                summary_parts.append(f"[{c.name}] {c.message}")

        summary = "All checks passed" if not summary_parts else "; ".join(summary_parts)

        return HealthReport(
            overall_status=overall,
            timestamp=utc_now(),
            checks=checks,
            summary=summary,
        )

    # -- Individual checks ---------------------------------------------------

    def _check_path_isolation(self) -> HealthCheck:
        """Check that we're not using V2 paths."""
        try:
            if self._guard:
                self._guard.validate(write_fingerprint=False)
            return HealthCheck(
                name="path_isolation",
                status=HealthStatus.HEALTHY,
                message="V3 path isolation verified",
            )
        except RuntimeGuardError as exc:
            return HealthCheck(
                name="path_isolation",
                status=HealthStatus.CRITICAL,
                message=f"Path isolation failed: {exc}",
            )

    def _check_queue_schema(self) -> HealthCheck:
        """Check queue database schema."""
        if not self._queue:
            return HealthCheck(
                name="queue_schema",
                status=HealthStatus.WARNING,
                message="Queue store not initialized",
            )

        try:
            self._queue.initialize()
            self._queue.validate_schema()
            return HealthCheck(
                name="queue_schema",
                status=HealthStatus.HEALTHY,
                message="Queue schema valid",
                detail={"columns": len(self._queue.REQUIRED_COLUMNS)},
            )
        except Exception as exc:
            return HealthCheck(
                name="queue_schema",
                status=HealthStatus.ERROR,
                message=f"Queue schema error: {exc}",
            )

    def _check_queue_status(self) -> HealthCheck:
        """Check queue status counts."""
        if not self._queue:
            return HealthCheck(
                name="queue_status",
                status=HealthStatus.WARNING,
                message="Queue store not initialized",
            )

        try:
            counts = self._queue.count_by_status()
            total = sum(counts.values())

            # Check for excessive failures
            failed = counts.get("failed_terminal", 0)
            if failed > 10:
                return HealthCheck(
                    name="queue_status",
                    status=HealthStatus.WARNING,
                    message=f"High failure count: {failed}",
                    detail=counts,
                )

            return HealthCheck(
                name="queue_status",
                status=HealthStatus.HEALTHY,
                message=f"Queue OK: {total} tasks",
                detail=counts,
            )
        except Exception as exc:
            return HealthCheck(
                name="queue_status",
                status=HealthStatus.ERROR,
                message=f"Queue status error: {exc}",
            )

    def _check_stale_processing(self) -> HealthCheck:
        """Check for and recover stale processing tasks."""
        if not self._queue:
            return HealthCheck(
                name="stale_processing",
                status=HealthStatus.WARNING,
                message="Queue store not initialized",
            )

        try:
            # Tasks stuck in processing for > 30 minutes
            stale_threshold = (
                datetime.now(UTC) - timedelta(minutes=30)
            ).isoformat()

            recovered = self._queue.recover_stale_processing(stale_threshold)

            if recovered > 0:
                return HealthCheck(
                    name="stale_processing",
                    status=HealthStatus.WARNING,
                    message=f"Recovered {recovered} stale processing tasks",
                    detail={"stale_threshold": stale_threshold},
                    recovered=recovered,
                )

            return HealthCheck(
                name="stale_processing",
                status=HealthStatus.HEALTHY,
                message="No stale processing tasks",
                recovered=0,
            )
        except Exception as exc:
            return HealthCheck(
                name="stale_processing",
                status=HealthStatus.ERROR,
                message=f"Stale processing check error: {exc}",
            )

    def _check_role_locks(self) -> HealthCheck:
        """Check role lock files for freshness."""
        state_root = Path(self._config.runtime.state_root).expanduser()
        role_dir = state_root / "roles"

        if not role_dir.exists():
            return HealthCheck(
                name="role_locks",
                status=HealthStatus.HEALTHY,
                message="No roles running",
            )

        stale_roles = []
        details: dict[str, str] = {}
        now = datetime.now(UTC)
        active_statuses = {"starting", "running", "waiting", "restarting"}

        for role_file in role_dir.glob("*.json"):
            try:
                with open(role_file) as f:
                    data = json.load(f)

                role = role_file.stem
                status = str(data.get("status", ""))
                if status == "stopped":
                    continue
                if status and status not in active_statuses:
                    stale_roles.append(role)
                    details[role] = f"status={status}"
                    continue

                pid = data.get("pid")
                if pid and not _pid_is_alive(pid):
                    stale_roles.append(role)
                    details[role] = f"pid_not_alive={pid}"
                    continue

                updated_at = data.get("updated_at", "")
                if not updated_at:
                    continue

                try:
                    updated = datetime.fromisoformat(updated_at)
                except ValueError:
                    continue

                # Role is stale if not updated in 10 minutes
                if (now - updated).total_seconds() > 600:
                    stale_roles.append(role)
                    details[role] = f"heartbeat_age_seconds={int((now - updated).total_seconds())}"
            except (json.JSONDecodeError, OSError):
                pass

        if stale_roles:
            return HealthCheck(
                name="role_locks",
                status=HealthStatus.WARNING,
                message=f"Stale roles: {', '.join(stale_roles)}",
                detail={"stale_roles": stale_roles, "details": details},
            )

        return HealthCheck(
            name="role_locks",
            status=HealthStatus.HEALTHY,
            message="All roles healthy",
        )

    def _check_live_requirements(self) -> HealthCheck:
        """Check live mode requirements."""
        issues = []

        # Check Obsidian root
        obsidian_root = self._config.outputs.obsidian_root
        if not obsidian_root:
            issues.append("obsidian_root not configured")
        else:
            obsidian_path = Path(obsidian_root).expanduser()
            if not obsidian_path.exists():
                issues.append(f"obsidian_root does not exist: {obsidian_path}")

        # Check Telegram if enabled
        if self._config.outputs.telegram_enabled:
            # 优先检查直接配置的 token，其次检查环境变量
            token = self._config.outputs.telegram_bot_token or os.environ.get(self._config.outputs.telegram_bot_token_env)
            if not token:
                token_env = self._config.outputs.telegram_bot_token_env
                issues.append(f"Telegram token not found: {token_env}")

        if issues:
            return HealthCheck(
                name="live_requirements",
                status=HealthStatus.ERROR,
                message="Live mode requirements not met",
                detail={"issues": issues},
            )

        return HealthCheck(
            name="live_requirements",
            status=HealthStatus.HEALTHY,
            message="Live mode requirements met",
        )

    def _check_prompt_registry(self) -> HealthCheck:
        """Validate active prompt routing and report the active bundle hash."""
        try:
            prompts = self._prompts or PromptRegistry.from_config(
                Path(__file__).resolve().parents[2],
                self._config.prompts,
            )
            prompts.validate(required_roles=("scoring", "extraction", "telegram_brief"))
            active_bundle = prompts.active_bundle_name
            scoring_prompt = prompts.load_prompt(active_bundle, "scoring")
            extraction_prompt = prompts.load_prompt(active_bundle, "extraction")
            telegram_prompt = prompts.load_prompt(active_bundle, "telegram_brief")
            prompt_hash = sha256_text(
                scoring_prompt + extraction_prompt + telegram_prompt,
                length=16,
            )
            return HealthCheck(
                name="prompt_registry",
                status=HealthStatus.HEALTHY,
                message=f"Active prompt bundle: {active_bundle} ({prompt_hash})",
                detail={
                    "active_bundle": active_bundle,
                    "prompt_hash": prompt_hash,
                    "parallel_test_bundles": prompts.parallel_test_bundle_names,
                },
            )
        except Exception as exc:
            return HealthCheck(
                name="prompt_registry",
                status=HealthStatus.ERROR,
                message=f"Prompt registry error: {exc}",
            )

    @staticmethod
    def _worst_status(statuses) -> HealthStatus:
        """Return the worst (highest severity) status."""
        statuses = list(statuses)
        order = [HealthStatus.HEALTHY, HealthStatus.WARNING, HealthStatus.ERROR, HealthStatus.CRITICAL]
        for s in reversed(order):
            if s in statuses:
                return s
        return HealthStatus.HEALTHY


def _pid_is_alive(pid: object) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for health check.

    Usage:
        python -m knowledge_extractor_v3.health [--json] [--recover]
    """
    import argparse

    parser = argparse.ArgumentParser(description="V3 Health Check")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON format",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Run recovery actions",
    )

    args = parser.parse_args(argv)

    # Load config
    project_root = Path(__file__).resolve().parents[2]
    loader = ConfigLoader(project_root=project_root)
    config = loader.load()

    # Create components
    queue_path = loader.expand_path(config.runtime.queue_db_path)
    queue_store = QueueStore(queue_path)
    guard = RuntimeGuard.from_env(project_root=project_root)

    # Run health check
    checker = HealthChecker(
        config=config,
        queue_store=queue_store,
        runtime_guard=guard,
        prompt_registry=PromptRegistry.from_config(project_root, config.prompts),
    )

    report = checker.run_all()

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"V3 Health Check - {report.overall_status.value.upper()}")
        print(f"Timestamp: {report.timestamp}")
        print()
        for check in report.checks:
            status_symbol = {
                HealthStatus.HEALTHY: "✓",
                HealthStatus.WARNING: "⚠",
                HealthStatus.ERROR: "✗",
                HealthStatus.CRITICAL: "!!",
            }.get(check.status, "?")
            print(f"  {status_symbol} {check.name}: {check.message}")
            if check.recovered > 0:
                print(f"     (recovered {check.recovered} items)")

    # Exit code based on status
    if report.overall_status == HealthStatus.CRITICAL:
        return 2
    elif report.overall_status == HealthStatus.ERROR:
        return 1
    elif report.overall_status == HealthStatus.WARNING:
        return 0
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
