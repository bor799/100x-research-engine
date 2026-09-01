"""Tests for LiveObsidianWriter and LiveOutputPort."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knowledge_extractor_v3.models import (
    ExtractionResult,
    FetchedContent,
    OutputResult,
    RuntimeMode,
    ScoreResult,
)
from knowledge_extractor_v3.outputs.live_obsidian import LiveObsidianWriter, LiveOutputPort
from knowledge_extractor_v3.outputs.wechat_queue import WechatQueue
from knowledge_extractor_v3.queue_store import FailureKind


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fetched(url: str = "https://example.com/article") -> FetchedContent:
    return FetchedContent(
        url=url,
        source="test",
        source_type="web_article",
        title="Test Article Title",
        text="Body text of the article for testing purposes.",
        fetched_at="2026-04-28T12:00:00+00:00",
        content_hash="abc123def456",
    )


def _score() -> ScoreResult:
    return ScoreResult(
        prompt_bundle="test_bundle",
        prompt_hash="hash123",
        model_route="test://model",
        raw_text="{}",
        parsed={},
        score=8.2,
        final_score=0.82,
        signal_tier="A",
        information_gain=0.82,
        action_value=0.80,
        relevance=0.78,
        rationale="测试用途。",
        is_spam=False,
    )


def _extraction() -> ExtractionResult:
    return ExtractionResult(
        prompt_bundle="test_bundle",
        prompt_hash="hash123",
        model_route="test://model",
        raw_text="{}",
        parsed={},
        title="Test Extraction Title",
        one_line_signal="Company raised $100M Series B",
        obsidian_brief_markdown="# Brief\n\nThis is a test brief.",
    )


# ---------------------------------------------------------------------------
# LiveObsidianWriter tests
# ---------------------------------------------------------------------------


class TestLiveObsidianWriter:
    def test_atomic_write(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox")
        result = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="test",
            prompt_hash="hash",
        )
        assert isinstance(result, str)
        output = Path(result)
        assert output.exists()
        from datetime import datetime
        from zoneinfo import ZoneInfo

        from knowledge_extractor_v3.outputs.push_ledger import week_label

        current_week = week_label(datetime.now(ZoneInfo("Asia/Shanghai")).date())
        assert output.parent.name == current_week  # calendar-proof
        assert output.parent.parent == tmp_path
        assert output.name.endswith(".md")
        assert ".tmp-" not in output.name
        # No temp files remain
        assert list((tmp_path / "inbox").glob(".tmp-*")) == []

    def test_content_correctness(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        result = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="test",
            prompt_hash="hash",
        )
        output = Path(result)
        content = output.read_text(encoding="utf-8")
        assert content.startswith("---")
        assert "title:" in content
        assert "Test Extraction Title" in content
        assert "final_score:" in content
        assert "# Brief" in content
        assert content.index("# Brief") < content.index("## 原文")
        assert "Body text of the article" in content

    def test_stays_under_root(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox")
        result = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="test",
            prompt_hash="hash",
        )
        output = Path(result)
        assert str(output).startswith(str(tmp_path))

    def test_manifest_written(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=True)
        result = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="test",
            prompt_hash="hash",
            task_id=42,
        )
        assert isinstance(result, str)
        manifest_path = Path(result).parent / "manifest.jsonl"
        assert manifest_path.exists()
        lines = manifest_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["task_id"] == 42
        assert entry["final_score"] == 0.82
        assert entry["prompt_bundle"] == "test"

    def test_no_manifest(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        result = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="test",
            prompt_hash="hash",
        )
        assert isinstance(result, str)
        manifest_path = Path(result).parent / "manifest.jsonl"
        assert not manifest_path.exists()

    def test_ignores_legacy_fixed_subdirectory_and_creates_week(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="deep/nested/path")
        result = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="test",
            prompt_hash="hash",
        )
        assert isinstance(result, str)
        assert Path(result).parent.parent == tmp_path
        assert not (tmp_path / "deep" / "nested" / "path").exists()


# ---------------------------------------------------------------------------


class TestLiveOutputPort:
    def test_wechat_only_queues_score_and_brief(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        queue_dir = tmp_path / "wechat"
        port = LiveOutputPort(
            obsidian_writer=writer,
            wechat_queue=WechatQueue(queue_dir),
            enqueue_individual_cards=True,
        )

        result = port.write(
            _fetched(),
            _score(),
            _extraction(),
            "微信简报",
            prompt_bundle="test",
            prompt_hash="hash",
            task_id=1,
            wechat_lane="business",
        )

        assert result.ok is True
        assert result.wechat_status == "queued"
        payload = json.loads(next((queue_dir / "pending").glob("*.json")).read_text(encoding="utf-8"))
        assert payload["text"] == "微信简报"
        assert payload["lane"] == "business"
        assert "final_score" in payload  # plan schema: final_score + action_value


    def test_wechat_queue_failure_prevents_done(self, tmp_path):
        queue_path = tmp_path / "queue-file"
        queue_path.write_text("occupied", encoding="utf-8")
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        port = LiveOutputPort(
            obsidian_writer=writer,
            wechat_queue=WechatQueue(queue_path),
            enqueue_individual_cards=True,
        )

        result = port.write(
            _fetched(), _score(), _extraction(), "brief",
            prompt_bundle="test", prompt_hash="hash", task_id=1,
            wechat_lane="business",
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.stage == "output.wechat_queue"







    def test_mode_is_live(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        port = LiveOutputPort(obsidian_writer=writer)
        assert port.mode is RuntimeMode.LIVE

    def test_default_routes_push_articles_to_magazine_without_new_card(self, tmp_path):
        queue_dir = tmp_path / "wechat"
        port = LiveOutputPort(
            obsidian_writer=LiveObsidianWriter(tmp_path, write_manifest=False),
            wechat_queue=WechatQueue(queue_dir),
        )
        result = port.write(
            _fetched(), _score(), _extraction(), "legacy card",
            prompt_bundle="test", prompt_hash="hash", task_id=1,
            wechat_lane="business",
        )
        assert result.ok is True
        assert result.wechat_status == "magazine_only"
        assert not list(queue_dir.glob("pending/*.json"))
