"""Tests for Telegram inbound bot with mocked HTTP."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from knowledge_extractor_v3.telegram_bot import (
    TelegramInboundBot,
    BotCommand,
    create_bot,
    _HTTPResponse,
)
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.config_loader import (
    V3Config,
    RuntimeConfig,
    LiveConfig,
    WorkerConfig as V3WorkerConfig,
    TelegramBotConfig as V3TelegramBotConfig,
)


class MockHTTPGet:
    """Mock HTTP GET for testing."""

    def __init__(self) -> None:
        self.updates_to_return = []
        self.get_calls = []

    def __call__(self, url: str, *, timeout: int) -> _HTTPResponse:
        self.get_calls.append(url)

        response = _HTTPResponse()
        response.status_code = 200

        # Return empty updates by default
        updates = getattr(self, "updates", [])
        response.body = '{"ok": true, "result": []}'

        if self.updates_to_return:
            import json
            response.body = json.dumps({"ok": True, "result": self.updates_to_return})

        return response


class MockHTTPPost:
    """Mock HTTP POST for testing."""

    def __init__(self) -> None:
        self.messages_sent = []

    def __call__(self, url: str, *, data: bytes, timeout: int) -> _HTTPResponse:
        import json

        self.messages_sent.append(json.loads(data.decode("utf-8")))

        response = _HTTPResponse()
        response.status_code = 200
        response.body = '{"ok": true, "result": {"message_id": 123}}'
        return response


def make_test_config(state_root: Path) -> V3Config:
    """Create minimal test config."""
    return V3Config(
        runtime=RuntimeConfig(
            state_root=str(state_root),
            queue_db_path=str(state_root / "queue.db"),
        ),
        live=LiveConfig(enabled=False),
        worker=V3WorkerConfig(),
    )


def test_bot_command_parsing():
    """Bot parses commands from updates."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())

    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
    )

    # Basic command
    update = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "chat": {"id": "123456"},
            "text": "/url https://example.com/article",
        },
    }

    cmd = bot._parse_command(update)

    assert cmd is not None
    assert cmd.chat_id == "123456"
    assert cmd.command == "url"
    assert cmd.args == "https://example.com/article"


def test_bot_direct_url_parsing():
    """Bot treats a plain URL message as a URL enqueue command."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())

    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
    )

    update = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "chat": {"id": "123456"},
            "text": "https://example.com/direct",
        },
    }

    cmd = bot._parse_command(update)

    assert cmd is not None
    assert cmd.command == "url"
    assert cmd.args == "https://example.com/direct"


def test_bot_url_inside_text_parsing():
    """Bot accepts a URL pasted inside ordinary text."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())

    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
    )

    update = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "chat": {"id": "123456"},
            "text": "帮我读一下 https://example.com/direct?x=1。",
        },
    }

    cmd = bot._parse_command(update)

    assert cmd is not None
    assert cmd.command == "url"
    assert cmd.args == "帮我读一下 https://example.com/direct?x=1。"


def test_bot_plain_text_input_gets_response():
    """Bot acknowledges text input instead of silently ignoring it."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())
    mock_post = MockHTTPPost()

    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
        http_post=mock_post,
    )

    update = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "chat": {"id": "123456"},
            "text": "hello v3",
        },
    }

    cmd = bot._parse_command(update)
    assert cmd is not None
    assert cmd.command == "input"

    bot._handle_command(cmd)

    assert len(mock_post.messages_sent) == 1
    assert "Text input is active" in mock_post.messages_sent[0].get("text", "")


def test_bot_command_unknown_chat():
    """Unknown chat is rejected when allowed_chat_ids is set."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())

    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
        allowed_chat_ids=["999999"],
    )

    update = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "chat": {"id": "123456"},
            "text": "/url https://example.com",
        },
    }

    cmd = bot._parse_command(update)
    assert cmd is not None
    assert cmd.is_allowed is False


def test_bot_command_allowed_chat():
    """Allowed chat is accepted."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())

    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
        allowed_chat_ids=["123456"],
    )

    update = {
        "update_id": 1,
        "message": {
            "message_id": 100,
            "chat": {"id": "123456"},
            "text": "/url https://example.com",
        },
    }

    cmd = bot._parse_command(update)
    assert cmd is not None
    assert cmd.is_allowed is True


def test_bot_cmd_url_enqueues():
    """Bot enqueues URL from /url command."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        mock_post = MockHTTPPost()
        bot = TelegramInboundBot(
            config=config,
            queue_store=queue_store,
            bot_token="test_token",
            http_post=mock_post,
        )

        cmd = BotCommand(
            chat_id="123456",
            command="url",
            args="https://example.com/test",
            message_id=100,
            is_allowed=True,
        )

        bot._handle_command(cmd)

        # Verify enqueued
        task = queue_store.find_by_url("https://example.com/test")
        assert task is not None
        assert task.source == "telegram_bot"
        assert task.priority == 10
        assert task.reply_channel == "telegram"
        assert task.reply_chat_id == "123456"

        # Verify response message
        assert len(mock_post.messages_sent) == 1
        assert "Enqueued" in mock_post.messages_sent[0].get("text", "")


def test_bot_cmd_url_enqueues_embedded_and_www_urls():
    """Bot enqueues URLs from prose and normalizes www links."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        mock_post = MockHTTPPost()
        bot = TelegramInboundBot(
            config=config,
            queue_store=queue_store,
            bot_token="test_token",
            http_post=mock_post,
        )

        cmd = BotCommand(
            chat_id="123456",
            command="url",
            args="读这两个：https://example.com/a， www.example.org/b",
            message_id=100,
            is_allowed=True,
        )

        bot._handle_command(cmd)

        assert queue_store.find_by_url("https://example.com/a") is not None
        assert queue_store.find_by_url("https://www.example.org/b") is not None
        assert len(mock_post.messages_sent) == 1
        assert "Enqueued URLs" in mock_post.messages_sent[0].get("text", "")


def test_bot_cmd_status():
    """Bot returns task status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        # Enqueue a task first
        task = queue_store.enqueue("https://example.com/test", source="test")

        mock_post = MockHTTPPost()
        bot = TelegramInboundBot(
            config=config,
            queue_store=queue_store,
            bot_token="test_token",
            http_post=mock_post,
        )

        cmd = BotCommand(
            chat_id="123456",
            command="status",
            args=str(task.id),
            message_id=100,
            is_allowed=True,
        )

        bot._handle_command(cmd)

        # Verify response
        assert len(mock_post.messages_sent) == 1
        text = mock_post.messages_sent[0].get("text", "")
        assert f"Task {task.id}" in text
        assert "pending" in text.lower()


def test_bot_cmd_recent():
    """Bot shows recent queue summary."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        # Enqueue some tasks
        queue_store.enqueue("https://example.com/1", source="test")
        queue_store.enqueue("https://example.com/2", source="test")

        mock_post = MockHTTPPost()
        bot = TelegramInboundBot(
            config=config,
            queue_store=queue_store,
            bot_token="test_token",
            http_post=mock_post,
        )

        cmd = BotCommand(
            chat_id="123456",
            command="recent",
            args="",
            message_id=100,
            is_allowed=True,
        )

        bot._handle_command(cmd)

        # Verify response
        assert len(mock_post.messages_sent) == 1
        text = mock_post.messages_sent[0].get("text", "")
        assert "Pending: 2" in text


def test_bot_cmd_help():
    """Bot shows help message."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())

    mock_post = MockHTTPPost()
    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
        http_post=mock_post,
    )

    cmd = BotCommand(
        chat_id="123456",
        command="help",
        args="",
        message_id=100,
        is_allowed=True,
    )

    bot._handle_command(cmd)

    # Verify response
    assert len(mock_post.messages_sent) == 1
    text = mock_post.messages_sent[0].get("text", "")
    assert "Commands:" in text
    assert "/url" in text


def test_bot_polling_loop():
    """Bot polling loop works."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        mock_get = MockHTTPGet()
        mock_post = MockHTTPPost()

        bot = TelegramInboundBot(
            config=config,
            queue_store=queue_store,
            bot_token="test_token",
            http_get=mock_get,
            http_post=mock_post,
        )

        # Run once with no updates
        processed = bot.run_loop(poll_interval_seconds=0, max_iterations=1)

        assert processed == 0


def test_bot_wait_uses_timed_sleep(monkeypatch):
    """Telegram polling wait should wake by timeout instead of blocking on a signal."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())
    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
    )
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        bot._shutdown_requested = True

    monkeypatch.setattr("knowledge_extractor_v3.telegram_bot.time.sleep", fake_sleep)

    bot._wait(30)

    assert sleeps
    assert sleeps[0] <= 1.0


def test_bot_unknown_command():
    """Bot handles unknown command."""
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    config = make_test_config(Path.cwd())

    mock_post = MockHTTPPost()
    bot = TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token="test_token",
        http_post=mock_post,
    )

    cmd = BotCommand(
        chat_id="123456",
        command="unknown",
        args="",
        message_id=100,
        is_allowed=True,
    )

    bot._handle_command(cmd)

    # Verify error response
    assert len(mock_post.messages_sent) == 1
    text = mock_post.messages_sent[0].get("text", "")
    assert "Unknown command" in text


if __name__ == "__main__":
    import traceback

    tests = [
        test_bot_command_parsing,
        test_bot_command_unknown_chat,
        test_bot_command_allowed_chat,
        test_bot_cmd_url_enqueues,
        test_bot_cmd_status,
        test_bot_cmd_recent,
        test_bot_cmd_help,
        test_bot_polling_loop,
        test_bot_unknown_command,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
            print(f"✓ {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"✗ {test.__name__}")
            traceback.print_exc()
        except Exception as e:
            failed += 1
            print(f"✗ {test.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
