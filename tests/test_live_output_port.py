"""Tests for LiveObsidianWriter, LiveTelegramClient, and LiveOutputPort."""

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
from knowledge_extractor_v3.outputs.telegram_live import LiveTelegramClient, _HTTPResponse
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
        score=7.5,
        final_score=0.82,
        signal_tier="Tier A",
        decision_window_status="open",
        source_type="web_article",
        source_tier="primary",
        interest_flag="high",
        attribution_chain=[],
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
        assert output.parent == tmp_path / "inbox"
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
        manifest_path = tmp_path / "inbox" / "manifest.jsonl"
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
        manifest_path = tmp_path / "inbox" / "manifest.jsonl"
        assert not manifest_path.exists()

    def test_creates_subdirectory(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="deep/nested/path")
        result = writer.write(
            _fetched(),
            _score(),
            _extraction(),
            prompt_bundle="test",
            prompt_hash="hash",
        )
        assert isinstance(result, str)
        assert (tmp_path / "deep" / "nested" / "path").exists()


# ---------------------------------------------------------------------------
# LiveTelegramClient tests
# ---------------------------------------------------------------------------


def _mock_http_post(status_code: int = 200, body: str = '{"ok":true}'):
    """Create a mock HTTP post callable."""
    def post(url: str, *, data: bytes, timeout: int) -> _HTTPResponse:
        resp = _HTTPResponse()
        resp.status_code = status_code
        resp.body = body
        return resp
    return post


def _capturing_http_post(messages: list[dict], status_code: int = 200):
    def post(url: str, *, data: bytes, timeout: int) -> _HTTPResponse:
        messages.append(json.loads(data.decode("utf-8")))
        resp = _HTTPResponse()
        resp.status_code = status_code
        resp.body = '{"ok":true}'
        return resp
    return post


class TestLiveTelegramClient:
    def test_success(self):
        client = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=True,
            http_post=_mock_http_post(200),
        )
        status, preview = client.deliver(_fetched(), "Test brief text")
        assert status == "sent"
        assert preview == "Test brief text"

    def test_http_failure(self):
        client = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=True,
            http_post=_mock_http_post(400, '{"ok":false}'),
        )
        result = client.deliver(_fetched(), "Test brief text")
        assert isinstance(result, object) and hasattr(result, "failure_kind")
        assert result.failure_kind == FailureKind.OUTPUT_FAILED

    def test_server_error_retryable(self):
        client = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=True,
            http_post=_mock_http_post(503, "Service Unavailable"),
        )
        result = client.deliver(_fetched(), "Test brief text")
        assert isinstance(result, object) and hasattr(result, "failure_kind")
        assert result.retryable is True

    def test_disabled_no_http_call(self):
        call_count = 0

        def counting_post(url, *, data, timeout):
            nonlocal call_count
            call_count += 1
            return _mock_http_post()(url, data=data, timeout=timeout)

        client = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=False,
            http_post=counting_post,
        )
        status, preview = client.deliver(_fetched(), "Test brief text")
        assert status == "disabled"
        assert call_count == 0

    def test_network_exception(self):
        def failing_post(url, *, data, timeout):
            raise ConnectionError("network unreachable")

        client = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=True,
            http_post=failing_post,
        )
        result = client.deliver(_fetched(), "Test brief text")
        assert isinstance(result, object) and hasattr(result, "failure_kind")
        assert result.failure_kind == FailureKind.OUTPUT_FAILED
        assert result.retryable is True

    def test_text_truncated_to_max_length(self):
        long_text = "x" * 5000

        def verify_payload(url, *, data, timeout):
            payload = json.loads(data)
            assert len(payload["text"]) <= 4096
            resp = _HTTPResponse()
            resp.status_code = 200
            resp.body = '{"ok":true}'
            return resp

        client = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=True,
            max_length=4096,
            http_post=verify_payload,
        )
        status, _ = client.deliver(_fetched(), long_text)
        assert status == "sent"

    def test_optional_chat_id_overrides_default(self):
        messages: list[dict] = []
        client = LiveTelegramClient(
            bot_token="test-token",
            chat_id="admin",
            enabled=True,
            http_post=_capturing_http_post(messages),
        )

        status, _ = client.deliver(_fetched(), "Test brief text", chat_id="reply-chat")

        assert status == "sent"
        assert messages[0]["chat_id"] == "reply-chat"


# ---------------------------------------------------------------------------
# LiveOutputPort tests
# ---------------------------------------------------------------------------


class TestLiveOutputPort:
    def test_full_flow_success(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        telegram = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=True,
            http_post=_mock_http_post(200),
        )
        port = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)

        result = port.write(
            _fetched(),
            _score(),
            _extraction(),
            "Telegram brief text",
            prompt_bundle="test",
            prompt_hash="hash",
            task_id=1,
        )

        assert isinstance(result, OutputResult)
        assert result.ok is True
        assert result.mode is RuntimeMode.LIVE
        assert result.obsidian_path
        assert Path(result.obsidian_path).exists()
        assert result.telegram_status == "sent"

    def test_reply_chat_id_is_used_for_telegram_delivery(self, tmp_path):
        messages: list[dict] = []
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        telegram = LiveTelegramClient(
            bot_token="test-token",
            chat_id="admin",
            enabled=True,
            http_post=_capturing_http_post(messages),
        )
        port = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)

        fetched = _fetched()
        fetched = FetchedContent(
            **{**fetched.__dict__, "metadata": {"reply_chat_id": "user-chat"}}
        )

        result = port.write(
            fetched,
            _score(),
            _extraction(),
            "Telegram brief text",
            prompt_bundle="test",
            prompt_hash="hash",
            task_id=1,
        )

        assert result.ok is True
        assert messages[0]["chat_id"] == "user-chat"

    def test_telegram_failure_prevents_done(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        telegram = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=True,
            http_post=_mock_http_post(400, '{"ok":false}'),
        )
        port = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)

        result = port.write(
            _fetched(),
            _score(),
            _extraction(),
            "Telegram brief text",
            prompt_bundle="test",
            prompt_hash="hash",
            task_id=1,
        )

        assert result.ok is False
        assert result.error is not None
        assert result.error.failure_kind == FailureKind.OUTPUT_FAILED
        # Obsidian was written despite telegram failure
        assert result.obsidian_path

    def test_telegram_disabled_obsidian_suffices(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        telegram = LiveTelegramClient(
            bot_token="test-token",
            chat_id="123",
            enabled=False,
        )
        port = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)

        result = port.write(
            _fetched(),
            _score(),
            _extraction(),
            "Telegram brief text",
            prompt_bundle="test",
            prompt_hash="hash",
            task_id=1,
        )

        assert result.ok is True
        assert result.telegram_status == "disabled"
        assert result.obsidian_path

    def test_no_telegram_client_obsidian_suffices(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        port = LiveOutputPort(obsidian_writer=writer, telegram_client=None)

        result = port.write(
            _fetched(),
            _score(),
            _extraction(),
            "Telegram brief text",
            prompt_bundle="test",
            prompt_hash="hash",
            task_id=1,
        )

        assert result.ok is True
        assert result.telegram_status == "not_configured"

    def test_obsidian_failure_prevents_telegram(self, tmp_path):
        # Write to a read-only directory to force failure
        writer = LiveObsidianWriter(tmp_path / "nonexistent", subdir="inbox")
        # Make the parent not existable (write will fail on mkdir)
        (tmp_path / "nonexistent").mkdir()
        (tmp_path / "nonexistent").chmod(0o000)

        try:
            port = LiveOutputPort(obsidian_writer=writer)

            result = port.write(
                _fetched(),
                _score(),
                _extraction(),
                "Telegram brief text",
                prompt_bundle="test",
                prompt_hash="hash",
                task_id=1,
            )

            assert result.ok is False
            assert result.error is not None
            assert result.error.failure_kind == FailureKind.OUTPUT_FAILED
        finally:
            (tmp_path / "nonexistent").chmod(0o755)

    def test_mode_is_live(self, tmp_path):
        writer = LiveObsidianWriter(tmp_path, subdir="inbox", write_manifest=False)
        port = LiveOutputPort(obsidian_writer=writer)
        assert port.mode is RuntimeMode.LIVE
