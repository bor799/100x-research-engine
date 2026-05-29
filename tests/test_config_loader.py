"""Tests for ConfigLoader and V3Config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from knowledge_extractor_v3.config_loader import (
    AgentReachConfig,
    ConfigLoader,
    ConfigLoaderError,
    LiveConfig,
    OutputsConfig,
    SchedulerConfig,
    ScoreGateConfig,
    V3Config,
    WorkerConfig,
    _deep_merge,
    _yaml_scalar,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_CONFIG = PROJECT_ROOT / "config" / "config.example.yaml"


def _make_local_config(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    # Copy example as base
    if EXAMPLE_CONFIG.exists():
        (config_dir / "config.example.yaml").write_text(
            EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
        )
    local = config_dir / "config.local.yaml"
    local.write_text(content, encoding="utf-8")
    return tmp_path


def _make_example_only(tmp_path: Path) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    if EXAMPLE_CONFIG.exists():
        (config_dir / "config.example.yaml").write_text(
            EXAMPLE_CONFIG.read_text(encoding="utf-8"), encoding="utf-8"
        )
    return tmp_path


# ---------------------------------------------------------------------------
# YAML scalar tests
# ---------------------------------------------------------------------------


class TestYamlScalar:
    def test_string(self):
        assert _yaml_scalar("hello") == "hello"

    def test_quoted_string(self):
        assert _yaml_scalar('"hello"') == "hello"

    def test_bool_true(self):
        assert _yaml_scalar("true") is True

    def test_bool_false(self):
        assert _yaml_scalar("false") is False

    def test_integer(self):
        assert _yaml_scalar("42") == 42

    def test_float(self):
        assert _yaml_scalar("2.5") == 2.5

    def test_empty(self):
        assert _yaml_scalar("") == ""


# ---------------------------------------------------------------------------
# Deep merge tests
# ---------------------------------------------------------------------------


class TestDeepMerge:
    def test_override_scalar(self):
        base = {"a": 1, "b": 2}
        override = {"b": 3}
        assert _deep_merge(base, override) == {"a": 1, "b": 3}

    def test_nested_merge(self):
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 3, "c": 4}}
        assert _deep_merge(base, override) == {"outer": {"a": 1, "b": 3, "c": 4}}

    def test_new_key(self):
        base = {"a": 1}
        override = {"b": 2}
        assert _deep_merge(base, override) == {"a": 1, "b": 2}


# ---------------------------------------------------------------------------
# ConfigLoader tests
# ---------------------------------------------------------------------------


class TestConfigLoader:
    def test_load_from_example_when_no_local(self, tmp_path):
        project = _make_example_only(tmp_path)
        loader = ConfigLoader(project_root=project)
        config = loader.load()

        assert isinstance(config, V3Config)
        assert config.runtime.state_root == "~/.100x_v3"
        assert config.prompts.active_bundle == "v2_stable_cn"
        assert loader.config_path_used.name == "config.example.yaml"

    def test_prompts_default_to_v2_stable_cn_without_config_section(self, tmp_path):
        config_file = tmp_path / "minimal.yaml"
        config_file.write_text(
            "\n".join([
                "runtime:",
                "  state_root: \"/explicit\"",
                "  queue_db_path: \"/explicit/q.db\"",
                "  log_path: \"/explicit/log\"",
            ]),
            encoding="utf-8",
        )
        loader = ConfigLoader(explicit_path=config_file)
        config = loader.load()

        assert config.prompts.active_bundle == "v2_stable_cn"

    def test_load_from_local_overrides_example(self, tmp_path):
        project = _make_local_config(tmp_path, "\n".join([
            "runtime:",
            "  state_root: \"/custom/state\"",
            "live:",
            "  enabled: true",
        ]))
        loader = ConfigLoader(project_root=project)
        config = loader.load()

        assert config.runtime.state_root == "/custom/state"
        assert config.live.enabled is True
        # Other fields still have example defaults
        assert config.live.max_consecutive_failures == 5
        assert loader.config_path_used.name == "config.local.yaml"
        assert loader.using_local_config is True

    def test_load_from_explicit_path(self, tmp_path):
        config_file = tmp_path / "custom.yaml"
        config_file.write_text("\n".join([
            "runtime:",
            "  state_root: \"/explicit\"",
            "  queue_db_path: \"/explicit/q.db\"",
            "  log_path: \"/explicit/log\"",
        ]), encoding="utf-8")
        loader = ConfigLoader(explicit_path=config_file)
        config = loader.load()

        assert config.runtime.state_root == "/explicit"
        assert loader.config_path_used == config_file

    def test_missing_config_raises(self, tmp_path):
        empty = tmp_path / "empty_project"
        empty.mkdir()
        loader = ConfigLoader(project_root=empty)
        with pytest.raises(ConfigLoaderError, match="No config file found"):
            loader.load()

    def test_missing_explicit_path_raises(self, tmp_path):
        missing = tmp_path / "nonexistent.yaml"
        loader = ConfigLoader(explicit_path=missing)
        with pytest.raises(ConfigLoaderError, match="not found"):
            loader.load()

    def test_resolve_env_returns_value(self):
        loader = ConfigLoader(env={"MY_KEY": "secret123"})
        assert loader.resolve_env("MY_KEY") == "secret123"

    def test_resolve_env_missing_returns_empty(self):
        loader = ConfigLoader(env={})
        assert loader.resolve_env("MISSING_KEY") == ""

    def test_expand_path_tilde(self):
        loader = ConfigLoader()
        expanded = loader.expand_path("~/test")
        # Check that tilde was expanded (path is absolute and contains 'test' at end)
        assert expanded.is_absolute()
        assert expanded.name == "test"
        # The parent should be the home directory (or equivalent via /private symlink on macOS)
        # Use os.path.samefile to handle symlink differences
        import os
        assert os.path.samefile(expanded.parent, Path.home())

    def test_expand_path_relative(self, tmp_path):
        loader = ConfigLoader(project_root=tmp_path)
        expanded = loader.expand_path("config/local.yaml")
        assert expanded == (tmp_path / "config" / "local.yaml").resolve()

    def test_live_defaults_to_disabled(self, tmp_path):
        project = _make_example_only(tmp_path)
        loader = ConfigLoader(project_root=project)
        config = loader.load()
        assert config.live.enabled is False

    def test_new_sections_have_defaults(self, tmp_path):
        project = _make_example_only(tmp_path)
        loader = ConfigLoader(project_root=project)
        config = loader.load()

        assert config.scheduler.enabled is False
        assert config.scheduler.interval_seconds == 300
        assert config.worker.batch_size == 10
        assert config.worker.poll_interval_seconds == 30
        assert config.telegram_bot.enabled is False
        assert isinstance(config.score_gate, ScoreGateConfig)
        assert config.score_gate.enabled is True
        assert config.score_gate.reject_threshold == 0.3
        assert isinstance(config.sources, list)

    def test_config_path_used_raises_before_load(self):
        loader = ConfigLoader()
        with pytest.raises(ConfigLoaderError, match="not loaded"):
            _ = loader.config_path_used

    def test_using_local_config_false_before_load(self):
        loader = ConfigLoader()
        assert loader.using_local_config is False

    def test_outputs_section_loaded(self, tmp_path):
        project = _make_example_only(tmp_path)
        loader = ConfigLoader(project_root=project)
        config = loader.load()

        assert config.outputs.obsidian_root == ""
        assert config.outputs.obsidian_subdir == "inbox"
        assert config.outputs.write_manifest is True
        assert config.outputs.telegram_enabled is True
        assert config.outputs.telegram_bot_token_env == "TELEGRAM_BOT_TOKEN"

    def test_llm_section_loaded(self, tmp_path):
        project = _make_example_only(tmp_path)
        loader = ConfigLoader(project_root=project)
        config = loader.load()

        assert config.llm.provider == "placeholder"
        assert config.llm.api_base == "https://open.bigmodel.cn/api/coding/paas/v4"
        assert config.llm.request_timeout_seconds == 60
        assert config.llm.max_retries == 3
        assert config.llm.min_delay_seconds == 2.0
        assert config.llm.temperature == 0.1

    def test_v2_llm_and_output_shape_is_normalized(self, tmp_path):
        project = _make_local_config(tmp_path, "\n".join([
            "runtime:",
            "  state_root: \"/tmp/state\"",
            "llm:",
            "  provider: zhipu",
            "  api_base: https://open.bigmodel.cn/api/coding/paas/v4",
            "  api_key: direct-key",
            "  api_key_env: ZHIPU_API_KEY",
            "  temperature: 0.1",
            "  router:",
            "    quality_filter:",
            "      model: GLM-4.5",
            "    deep_analysis:",
            "      model: GLM-4.7",
            "    telegram_format:",
            "      model: GLM-4.5-Air",
            "output:",
            "  obsidian_root: /tmp/obsidian",
            "  obsidian_folder: AI进展",
            "filter:",
            "  scoring_prompt: config/prompts/scoring.md",
            "extraction_prompt: config/prompts/extraction.md",
        ]))

        config = ConfigLoader(project_root=project).load()

        assert config.llm.provider == "zhipu"
        assert config.llm.api_key == "direct-key"
        assert config.llm.api_base == "https://open.bigmodel.cn/api/coding/paas/v4"
        assert config.llm.scoring_model == "GLM-4.5"
        assert config.llm.extraction_model == "GLM-4.7"
        assert config.llm.telegram_brief_model == "GLM-4.5-Air"
        assert config.outputs.obsidian_root == "/tmp/obsidian"
        assert config.outputs.obsidian_subdir == "AI进展"
        assert config.prompts.scoring == "config/prompts/scoring.md"
        assert config.prompts.extraction == "config/prompts/extraction.md"

    def test_v2_telegram_token_and_chat_id_are_normalized(self, tmp_path):
        """V2 used output.telegram_token and output.telegram_chat_id directly."""
        project = _make_local_config(tmp_path, "\n".join([
            "runtime:",
            "  state_root: \"/tmp/state\"",
            "output:",
            "  obsidian_root: /tmp/obsidian",
            "  obsidian_folder: AI进展",
            "  telegram_token: \"123456:ABC-DEF\"",
            "  telegram_chat_id: \"123456789\"",
        ]))

        config = ConfigLoader(project_root=project).load()

        # V2 的 telegram_token 应该映射到 telegram_bot_token
        assert config.outputs.telegram_bot_token == "123456:ABC-DEF"
        # V2 的 telegram_chat_id 应该映射到 telegram_admin_chat_id
        assert config.outputs.telegram_admin_chat_id == "123456789"
        # 环境变量配置应该保持默认值
        assert config.outputs.telegram_bot_token_env == "TELEGRAM_BOT_TOKEN"
        assert config.outputs.telegram_admin_chat_id_env == "TELEGRAM_ADMIN_CHAT_ID"

    def test_agent_reach_section_loaded(self, tmp_path):
        project = _make_local_config(tmp_path, "\n".join([
            "runtime:",
            "  state_root: \"/tmp/state\"",
            "agent_reach:",
            "  enabled: true",
            "  config_path: \"/tmp/agent-reach.yaml\"",
            "  enabled_channels:",
            "    - youtube",
            "    - twitter",
            "  fallback_to_jina: false",
            "  proxy: \"http://127.0.0.1:7890\"",
        ]))

        config = ConfigLoader(project_root=project).load()

        assert isinstance(config.agent_reach, AgentReachConfig)
        assert config.agent_reach.enabled is True
        assert config.agent_reach.config_path == "/tmp/agent-reach.yaml"
        assert config.agent_reach.enabled_channels == ["youtube", "twitter"]
        assert config.agent_reach.fallback_to_jina is False
        assert config.agent_reach.proxy == "http://127.0.0.1:7890"

    def test_score_gate_section_loaded(self, tmp_path):
        project = _make_local_config(tmp_path, "\n".join([
            "runtime:",
            "  state_root: \"/tmp/state\"",
            "score_gate:",
            "  enabled: false",
            "  reject_threshold: 0.6",
        ]))

        config = ConfigLoader(project_root=project).load()

        assert config.score_gate.enabled is False
        assert config.score_gate.reject_threshold == 0.6

    def test_legacy_score_threshold_is_normalized_to_final_score_scale(self, tmp_path):
        project = _make_local_config(tmp_path, "\n".join([
            "runtime:",
            "  state_root: \"/tmp/state\"",
            "score_threshold: 6.0",
        ]))

        config = ConfigLoader(project_root=project).load()

        assert config.score_gate.reject_threshold == 0.6

    def test_external_sources_file_is_loaded_and_deduped(self, tmp_path):
        project = _make_local_config(tmp_path, "\n".join([
            "runtime:",
            "  state_root: \"/tmp/state\"",
            "sources:",
            "  - name: Inline",
            "    type: rss",
            "    url: https://example.com/feed.xml",
            "sources_files:",
            "  - config/sources.yaml",
        ]))
        sources_file = project / "config" / "sources.yaml"
        sources_file.write_text("\n".join([
            "sources:",
            "- name: Inline Duplicate",
            "  type: rss",
            "  url: https://example.com/feed.xml",
            "- name: External",
            "  type: rss",
            "  url: https://example.com/external.xml",
            "  priority: 50",
            "  tags:",
            "    - ai",
            "  category: research",
        ]), encoding="utf-8")

        config = ConfigLoader(project_root=project).load()

        assert len(config.sources) == 2
        external = next(item for item in config.sources if item.name == "External")
        assert external.priority == 50
        assert external.tags == ["ai"]
        assert external.category == "research"
