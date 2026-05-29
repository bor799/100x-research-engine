"""Live gate: pre-flight safety checks before allowing LIVE mode.

Every check must pass for LIVE to proceed. Each check is individually
testable as a public method. The gate is designed so that live mode
refuses by default — live.enabled defaults to false.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config_loader import ConfigLoader, V3Config
    from .runtime_guard import RuntimeGuard


@dataclass(frozen=True)
class LiveGateCheck:
    """Result of a single live gate check."""

    name: str
    passed: bool
    message: str


@dataclass(frozen=True)
class LiveGateResult:
    """Aggregate result of all live gate checks."""

    passed: bool
    checks: list[LiveGateCheck]
    rejection_reasons: list[str]


class LiveGate:
    """Pre-flight safety checks before allowing LIVE mode.

    All checks must pass for LIVE mode to be unlocked. The default
    config has live.enabled=false, so LiveGate.check() will fail
    unless explicitly enabled.
    """

    def __init__(
        self,
        config: "V3Config",
        *,
        config_loader: "ConfigLoader",
        runtime_guard: "RuntimeGuard",
    ) -> None:
        self._config = config
        self._loader = config_loader
        self._guard = runtime_guard

    def check(self) -> LiveGateResult:
        """Run all checks and return aggregate result."""
        checks = [
            self.check_live_enabled(),
            self.check_local_config_exists(),
            self.check_runtime_guard(),
            self.check_obsidian_root(),
            self.check_obsidian_not_v2(),
            self.check_env_secrets(),
        ]
        rejection_reasons = [
            f"[{c.name}] {c.message}" for c in checks if not c.passed
        ]
        return LiveGateResult(
            passed=len(rejection_reasons) == 0,
            checks=checks,
            rejection_reasons=rejection_reasons,
        )

    # -- Individual checks -----------------------------------------------

    def check_live_enabled(self) -> LiveGateCheck:
        """live.enabled must be True in config."""
        if not self._config.live.enabled:
            return LiveGateCheck(
                name="live_enabled",
                passed=False,
                message="live.enabled is false in config",
            )
        return LiveGateCheck(name="live_enabled", passed=True, message="ok")

    def check_local_config_exists(self) -> LiveGateCheck:
        """config must be loaded from config.local.yaml, not example."""
        if not self._loader.using_local_config:
            return LiveGateCheck(
                name="local_config_exists",
                passed=False,
                message="config/config.local.yaml does not exist; only example config loaded",
            )
        return LiveGateCheck(name="local_config_exists", passed=True, message="ok")

    def check_runtime_guard(self) -> LiveGateCheck:
        """RuntimeGuard.validate(write_fingerprint=True) must pass."""
        from .runtime_guard import RuntimeGuardError

        try:
            self._guard.validate(write_fingerprint=True)
        except RuntimeGuardError as exc:
            return LiveGateCheck(
                name="runtime_guard",
                passed=False,
                message=str(exc),
            )
        return LiveGateCheck(name="runtime_guard", passed=True, message="ok")

    def check_obsidian_root(self) -> LiveGateCheck:
        """outputs.obsidian_root must be set and exist on disk."""
        raw = self._config.outputs.obsidian_root
        if not raw:
            return LiveGateCheck(
                name="obsidian_root",
                passed=False,
                message="outputs.obsidian_root is empty",
            )
        expanded = self._loader.expand_path(raw)
        if not expanded.exists():
            return LiveGateCheck(
                name="obsidian_root",
                passed=False,
                message=f"outputs.obsidian_root does not exist: {expanded}",
            )
        return LiveGateCheck(name="obsidian_root", passed=True, message="ok")

    def check_obsidian_not_v2(self) -> LiveGateCheck:
        """outputs.obsidian_root must not contain V2 markers."""
        raw = self._config.outputs.obsidian_root
        if not raw:
            return LiveGateCheck(name="obsidian_not_v2", passed=True, message="skipped (no path)")

        expanded = self._loader.expand_path(raw)
        if self._contains_v2_marker(expanded):
            return LiveGateCheck(
                name="obsidian_not_v2",
                passed=False,
                message=f"obsidian_root points at V2 path: {expanded}",
            )
        return LiveGateCheck(name="obsidian_not_v2", passed=True, message="ok")

    def check_env_secrets(self) -> LiveGateCheck:
        """All env vars referenced in config must exist and be non-empty."""
        env_vars = []
        if not getattr(self._config.llm, "api_key", ""):
            env_vars.append(self._config.llm.api_key_env)
        # 只有在没有直接配置 token 时才检查环境变量
        if not getattr(self._config.outputs, "telegram_bot_token", ""):
            env_vars.append(self._config.outputs.telegram_bot_token_env)
        if not getattr(self._config.outputs, "telegram_admin_chat_id", ""):
            env_vars.append(self._config.outputs.telegram_admin_chat_id_env)
        missing = [name for name in env_vars if not self._loader.resolve_env(name)]
        if missing:
            return LiveGateCheck(
                name="env_secrets",
                passed=False,
                message=f"missing or empty env vars: {', '.join(missing)}",
            )
        return LiveGateCheck(name="env_secrets", passed=True, message="ok")

    # -- internal --------------------------------------------------------

    @staticmethod
    def _contains_v2_marker(path: Path) -> bool:
        text = str(path.expanduser())
        return ".100x_v2" in text or "/knowledge-extractor/v2" in text
