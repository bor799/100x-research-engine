"""Tests for LiveGate pre-flight safety checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knowledge_extractor_v3.config_loader import (
    ConfigLoader,
    LiveConfig,
    LLMConfig,
    OutputsConfig,
    RuntimeConfig,
    V3Config,
)
from knowledge_extractor_v3.live_gate import LiveGate, LiveGateCheck, LiveGateResult
from knowledge_extractor_v3.runtime_guard import RuntimeGuard, RuntimeGuardError, RuntimePaths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    *,
    live_enabled: bool = False,
    obsidian_root: str = "",
    api_key_env: str = "ZHIPU_API_KEY",
    api_key: str = "",
    telegram_token_env: str = "TELEGRAM_BOT_TOKEN",
    telegram_chat_id_env: str = "TELEGRAM_ADMIN_CHAT_ID",
) -> V3Config:
    return V3Config(
        runtime=RuntimeConfig(),
        live=LiveConfig(enabled=live_enabled),
        llm=LLMConfig(api_key_env=api_key_env, api_key=api_key),
        outputs=OutputsConfig(
            obsidian_root=obsidian_root,
            telegram_bot_token_env=telegram_token_env,
            telegram_admin_chat_id_env=telegram_chat_id_env,
        ),
    )


def _make_loader(
    *,
    using_local: bool = True,
    env: dict[str, str] | None = None,
    project_root: Path | None = None,
) -> ConfigLoader:
    loader = ConfigLoader(
        project_root=project_root or Path("/fake/project"),
        env=env or {},
    )
    # Simulate having loaded from local config
    loader._config_path_used = (
        Path("/fake/project/config/config.local.yaml")
        if using_local
        else Path("/fake/project/config/config.example.yaml")
    )
    return loader


def _make_guard(should_fail: bool = False) -> RuntimeGuard:
    guard = MagicMock(spec=RuntimeGuard)
    if should_fail:
        guard.validate.side_effect = RuntimeGuardError("guard failed")
    else:
        guard.validate.return_value = None
    return guard


# ---------------------------------------------------------------------------
# Individual check tests
# ---------------------------------------------------------------------------


class TestCheckLiveEnabled:
    def test_disabled(self):
        config = _make_config(live_enabled=False)
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=_make_guard())
        check = gate.check_live_enabled()
        assert check.name == "live_enabled"
        assert not check.passed
        assert "false" in check.message

    def test_enabled(self):
        config = _make_config(live_enabled=True)
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=_make_guard())
        check = gate.check_live_enabled()
        assert check.passed


class TestCheckLocalConfigExists:
    def test_example_only_fails(self):
        config = _make_config()
        loader = _make_loader(using_local=False)
        gate = LiveGate(config, config_loader=loader, runtime_guard=_make_guard())
        check = gate.check_local_config_exists()
        assert not check.passed
        assert "local" in check.message.lower()

    def test_local_exists_passes(self):
        config = _make_config()
        loader = _make_loader(using_local=True)
        gate = LiveGate(config, config_loader=loader, runtime_guard=_make_guard())
        check = gate.check_local_config_exists()
        assert check.passed


class TestCheckRuntimeGuard:
    def test_guard_failure(self):
        config = _make_config()
        guard = _make_guard(should_fail=True)
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=guard)
        check = gate.check_runtime_guard()
        assert not check.passed
        assert "guard" in check.message.lower()

    def test_guard_passes(self):
        config = _make_config()
        guard = _make_guard(should_fail=False)
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=guard)
        check = gate.check_runtime_guard()
        assert check.passed


class TestCheckObsidianRoot:
    def test_empty_root_fails(self):
        config = _make_config(obsidian_root="")
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=_make_guard())
        check = gate.check_obsidian_root()
        assert not check.passed
        assert "empty" in check.message.lower()

    def test_nonexistent_root_fails(self, tmp_path):
        config = _make_config(obsidian_root=str(tmp_path / "nonexistent"))
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=_make_guard())
        check = gate.check_obsidian_root()
        assert not check.passed
        assert "does not exist" in check.message

    def test_existing_root_passes(self, tmp_path):
        config = _make_config(obsidian_root=str(tmp_path))
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=_make_guard())
        check = gate.check_obsidian_root()
        assert check.passed


class TestCheckObsidianNotV2:
    def test_v2_path_fails(self):
        config = _make_config(obsidian_root="/home/user/.100x_v2/obsidian")
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=_make_guard())
        check = gate.check_obsidian_not_v2()
        assert not check.passed
        assert "V2" in check.message

    def test_v3_path_passes(self, tmp_path):
        config = _make_config(obsidian_root=str(tmp_path))
        gate = LiveGate(config, config_loader=_make_loader(), runtime_guard=_make_guard())
        check = gate.check_obsidian_not_v2()
        assert check.passed


class TestCheckEnvSecrets:
    def test_missing_env_vars_fails(self):
        config = _make_config()
        loader = _make_loader(env={})
        gate = LiveGate(config, config_loader=loader, runtime_guard=_make_guard())
        check = gate.check_env_secrets()
        assert not check.passed
        assert "missing" in check.message.lower()
        assert "ZHIPU_API_KEY" in check.message

    def test_all_env_vars_present_passes(self):
        config = _make_config()
        loader = _make_loader(env={
            "ZHIPU_API_KEY": "test-key",
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_ADMIN_CHAT_ID": "123",
        })
        gate = LiveGate(config, config_loader=loader, runtime_guard=_make_guard())
        check = gate.check_env_secrets()
        assert check.passed

    def test_direct_llm_api_key_skips_llm_env_requirement(self):
        config = _make_config(api_key="direct-key")
        loader = _make_loader(env={
            "TELEGRAM_BOT_TOKEN": "test-token",
            "TELEGRAM_ADMIN_CHAT_ID": "123",
        })
        gate = LiveGate(config, config_loader=loader, runtime_guard=_make_guard())
        check = gate.check_env_secrets()
        assert check.passed


# ---------------------------------------------------------------------------
# Aggregate check tests
# ---------------------------------------------------------------------------


class TestCheckAggregate:
    def test_all_fail_when_live_disabled(self):
        config = _make_config(live_enabled=False)
        loader = _make_loader(env={})
        gate = LiveGate(config, config_loader=loader, runtime_guard=_make_guard())
        result = gate.check()

        assert isinstance(result, LiveGateResult)
        assert not result.passed
        assert len(result.rejection_reasons) >= 1
        assert any("live_enabled" in r for r in result.rejection_reasons)

    def test_multiple_failures_all_listed(self):
        config = _make_config(live_enabled=False, obsidian_root="")
        loader = _make_loader(using_local=False, env={})
        gate = LiveGate(config, config_loader=loader, runtime_guard=_make_guard())
        result = gate.check()

        assert not result.passed
        assert len(result.rejection_reasons) >= 2
        assert len(result.checks) == 6

    def test_all_pass(self, tmp_path):
        config = _make_config(live_enabled=True, obsidian_root=str(tmp_path))
        loader = _make_loader(
            using_local=True,
            env={
                "ZHIPU_API_KEY": "test-key",
                "TELEGRAM_BOT_TOKEN": "test-token",
                "TELEGRAM_ADMIN_CHAT_ID": "123",
            },
        )
        guard = _make_guard(should_fail=False)
        gate = LiveGate(config, config_loader=loader, runtime_guard=guard)
        result = gate.check()

        assert result.passed
        assert result.rejection_reasons == []
        assert all(c.passed for c in result.checks)
