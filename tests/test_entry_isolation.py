"""Regression tests for V3 entry-point isolation and observability.

Four categories:
  D1. Shadow HOME rejection — guard blocks worker/bot/scheduler startup
  D2. Path consistency — enqueue and worker resolve the same queue
  D3. Provider safety — test providers fail for real URLs by default
  D4. End-to-end observability — output files contain runtime metadata
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from knowledge_extractor_v3.config_loader import ConfigLoader, V3Config
from knowledge_extractor_v3.llm.provider import StubLLMProvider
from knowledge_extractor_v3.llm.shadow import ShadowHeuristicLLMProvider
from knowledge_extractor_v3.models import RuntimeMode
from knowledge_extractor_v3.outputs.obsidian import (
    DryRunOutputPort,
    StagingObsidianWriter,
    StagingOutputPort,
    _render_markdown,
)
from knowledge_extractor_v3.outputs.live_obsidian import (
    LiveObsidianWriter,
    LiveOutputPort,
)
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.runtime_guard import (
    RuntimeGuard,
    RuntimeGuardError,
    RuntimePaths,
    resolve_runtime_paths,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_paths(tmp_path: Path, **overrides) -> RuntimePaths:
    project_root = overrides.pop("project_root", tmp_path / "project")
    home = overrides.pop("home", tmp_path / "home")
    state_root = overrides.pop("state_root", home / ".100x_v3")
    queue_db_path = overrides.pop("queue_db_path", state_root / "queue.db")
    log_path = overrides.pop("log_path", home / "100x-v3-daemon.log")
    config_path = overrides.pop("config_path", project_root / "config" / "config.local.yaml")

    for p in (project_root, home, state_root):
        p.mkdir(parents=True, exist_ok=True)

    return RuntimePaths(
        project_root=project_root,
        state_root=state_root,
        queue_db_path=queue_db_path,
        log_path=log_path,
        config_path=config_path,
        home=home,
    )


def _stub_config() -> V3Config:
    """Minimal config for testing without file I/O."""
    return V3Config()


def _fetched(url: str = "https://example.com/article"):
    from knowledge_extractor_v3.models import FetchedContent
    from knowledge_extractor_v3.models import utc_now

    return FetchedContent(
        url=url,
        source="test",
        source_type="web",
        title="Test Article",
        text="Some body text that is long enough to pass validation checks.",
        fetched_at=utc_now(),
        content_hash="abc123",
    )


def _score():
    from knowledge_extractor_v3.models import ScoreResult

    return ScoreResult(
        score=7.0,
        final_score=7.5,
        signal_tier="A",
        model_route="stub://test",
        prompt_bundle="default",
        prompt_hash="deadbeef",
        raw_text="score: 7.0",
        parsed={"source_score": 7},
        decision_window_status="open",
        source_type="web",
        source_tier="A",
        interest_flag="high",
        attribution_chain=[],
    )


def _extraction():
    from knowledge_extractor_v3.models import ExtractionResult

    return ExtractionResult(
        title="Test Article",
        one_line_signal="Test signal",
        obsidian_brief_markdown="# Test\n\nBrief.",
        parsed={"source_score": 7},
        model_route="stub://test",
        prompt_bundle="default",
        prompt_hash="deadbeef",
        raw_text="title: Test Article",
    )


# ---------------------------------------------------------------------------
# D1. Shadow HOME rejection
# ---------------------------------------------------------------------------


class TestShadowHomeRejection:
    def test_shadow_home_rejects_worker_startup(self, tmp_path: Path):
        home = tmp_path / "codepilot-shadow-xyz"
        paths = _make_paths(tmp_path, home=home, state_root=home / ".100x_v3")

        with pytest.raises(RuntimeGuardError, match="shadow HOME"):
            RuntimeGuard(paths).validate()

    def test_shadow_home_does_not_create_shadow_queue(self, tmp_path: Path):
        home = tmp_path / "codepilot-shadow-xyz"
        state_root = home / ".100x_v3"
        paths = _make_paths(tmp_path, home=home, state_root=state_root)

        with pytest.raises(RuntimeGuardError):
            RuntimeGuard(paths).validate()

        assert not paths.queue_db_path.exists()


# ---------------------------------------------------------------------------
# D2. Path consistency
# ---------------------------------------------------------------------------


class TestPathConsistency:
    def test_resolve_runtime_paths_is_idempotent(self, tmp_path: Path):
        """Two calls with same inputs return the same queue_db_path."""
        # Use the real project root where config files exist
        project_root = Path(__file__).resolve().parents[1]
        home = tmp_path / "home"
        home.mkdir()

        env = {"HOME": str(home)}
        loader = ConfigLoader(project_root=project_root)
        config = loader.load()
        paths_a = resolve_runtime_paths(project_root, config, loader, env=env)
        paths_b = resolve_runtime_paths(project_root, config, loader, env=env)

        assert paths_a.queue_db_path == paths_b.queue_db_path

    def test_env_override_explicit_flag(self, tmp_path: Path):
        project_root = Path(__file__).resolve().parents[1]
        home = tmp_path / "home"
        home.mkdir()

        env_no_override = {"HOME": str(home)}
        env_override = {
            "HOME": str(home),
            "QUEUE_DB_PATH": str(tmp_path / "custom" / "queue.db"),
        }
        loader = ConfigLoader(project_root=project_root)
        config = loader.load()

        paths_default = resolve_runtime_paths(project_root, config, loader, env=env_no_override)
        paths_overridden = resolve_runtime_paths(project_root, config, loader, env=env_override)

        assert not paths_default.queue_db_is_explicit
        assert paths_overridden.queue_db_is_explicit
        assert paths_overridden.queue_db_path == Path(env_override["QUEUE_DB_PATH"])


# ---------------------------------------------------------------------------
# D3. Provider safety
# ---------------------------------------------------------------------------


class TestProviderSafety:
    def test_real_url_with_stub_provider_fails_terminal(self, tmp_path: Path):
        queue = QueueStore(tmp_path / "q.db")
        pipeline = Pipeline(
            queue_store=queue,
            llm_provider=StubLLMProvider(),
            allow_test_provider=False,
        )
        result = pipeline.process_url("https://example.com/real-article")
        from knowledge_extractor_v3.queue_store import QueueStatus

        assert result.final_status == QueueStatus.FAILED_TERMINAL
        assert "Test provider" in (result.error.message if result.error else "")

    def test_real_url_with_shadow_provider_fails_terminal(self, tmp_path: Path):
        queue = QueueStore(tmp_path / "q.db")
        pipeline = Pipeline(
            queue_store=queue,
            llm_provider=ShadowHeuristicLLMProvider(),
            allow_test_provider=False,
        )
        result = pipeline.process_url("https://example.com/real-article")
        from knowledge_extractor_v3.queue_store import QueueStatus

        assert result.final_status == QueueStatus.FAILED_TERMINAL

    def test_fixture_url_bypasses_provider_guard(self, tmp_path: Path):
        queue = QueueStore(tmp_path / "q.db")
        pipeline = Pipeline(
            queue_store=queue,
            llm_provider=StubLLMProvider(),
            allow_test_provider=False,
        )
        result = pipeline.process_url("fixture://sample-article")
        from knowledge_extractor_v3.queue_store import QueueStatus

        # fixture URLs should proceed past the guard (may fail later stages, that's OK)
        assert result.current_stage != "runtime_guard"

    def test_allow_test_provider_bypasses_guard(self, tmp_path: Path):
        queue = QueueStore(tmp_path / "q.db")
        pipeline = Pipeline(
            queue_store=queue,
            llm_provider=StubLLMProvider(),
            allow_test_provider=True,
        )
        result = pipeline.process_url("https://example.com/real-article")
        from knowledge_extractor_v3.queue_store import QueueStatus

        assert result.current_stage != "runtime_guard"


# ---------------------------------------------------------------------------
# D4. End-to-end observability
# ---------------------------------------------------------------------------


class TestObservability:
    def test_staging_frontmatter_contains_observability(self, tmp_path: Path):
        md = _render_markdown(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="default",
            prompt_hash="deadbeef",
            processed_at="2026-05-10T12:00:00",
            runtime_mode="staging",
            provider_route="stub://test",
            is_test_provider=True,
            runtime_fingerprint="fp-abc123",
        )
        assert "runtime_mode:" in md
        assert "provider_route:" in md
        assert "is_test_provider:" in md
        assert "runtime_fingerprint:" in md

    def test_staging_output_port_passes_observability(self, tmp_path: Path):
        port = StagingOutputPort(tmp_path)
        result = port.write(
            _fetched(),
            _score(),
            _extraction(),
            "telegram text",
            prompt_bundle="default",
            prompt_hash="deadbeef",
            task_id=1,
            runtime_mode="staging",
            provider_route="stub://test",
            is_test_provider=True,
            runtime_fingerprint="fp-xyz",
        )
        assert result.ok
        # Read the written file
        obsidian_dir = tmp_path / "obsidian"
        files = list(obsidian_dir.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "runtime_mode:" in content

    def test_live_manifest_contains_observability(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        writer = LiveObsidianWriter(root=tmp_path, subdir="inbox", write_manifest=True)
        output = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="default",
            prompt_hash="deadbeef",
            task_id=42,
            runtime_mode="live",
            provider_route="live://gemini-2.5-pro",
            is_test_provider=True,
            runtime_fingerprint="fp-live-run",
        )
        assert isinstance(output, str)

        manifest_path = inbox / "manifest.jsonl"
        assert manifest_path.exists()
        line = manifest_path.read_text().strip().split("\n")[-1]
        entry = json.loads(line)
        assert entry["runtime_mode"] == "live"
        assert entry["provider_route"] == "live://gemini-2.5-pro"
        assert entry["is_test_provider"] is True
        assert "fp-live-run" in entry["runtime_fingerprint"]

    def test_live_output_port_passes_observability(self, tmp_path: Path):
        inbox = tmp_path / "inbox"
        inbox.mkdir()
        writer = LiveObsidianWriter(root=tmp_path, subdir="inbox", write_manifest=True)
        port = LiveOutputPort(obsidian_writer=writer, telegram_client=None)
        result = port.write(
            _fetched(),
            _score(),
            _extraction(),
            "telegram text",
            prompt_bundle="default",
            prompt_hash="deadbeef",
            task_id=1,
            runtime_mode="live",
            provider_route="live://test",
            is_test_provider=False,
            runtime_fingerprint="fp-123",
        )
        assert result.ok
        files = list(inbox.glob("*.md"))
        assert len(files) == 1
        content = files[0].read_text()
        assert "runtime_mode:" in content
