"""Telegram inbound bot for manual URL submissions.

The bot:
- Accepts commands from allowed chat IDs
- Enqueues URLs with high priority
- Sets reply_channel="telegram" for worker responses
- Does NOT process tasks inline (worker does that)
"""

from __future__ import annotations

import json
import re
import signal
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .config_loader import ConfigLoader, V3Config
from .models import utc_now
from .queue_store import QueueStore
from .runtime_guard import RuntimeGuard, RuntimeGuardError, resolve_runtime_paths

if TYPE_CHECKING:
    from .sources.models import SchedulerEvent


# ---------------------------------------------------------------------------
# HTTP abstraction for polling/updates
# ---------------------------------------------------------------------------


class _HTTPResponse:
    """Minimal response interface."""
    status_code: int
    body: str


class _HTTPGet(Protocol):
    def __call__(self, url: str, *, timeout: int) -> _HTTPResponse: ...


class _HTTPPost(Protocol):
    def __call__(
        self,
        url: str,
        *,
        data: bytes,
        timeout: int,
    ) -> _HTTPResponse: ...


def _default_http_get(url: str, *, timeout: int) -> _HTTPResponse:
    """Real HTTP GET using urllib."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            response = _HTTPResponse()
            response.status_code = resp.status
            response.body = body
            return response
    except urllib.error.HTTPError as exc:
        response = _HTTPResponse()
        response.status_code = exc.code
        response.body = exc.read().decode("utf-8")
        return response


def _default_http_post(url: str, *, data: bytes, timeout: int) -> _HTTPResponse:
    """Real HTTP POST using urllib."""
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            response = _HTTPResponse()
            response.status_code = resp.status
            response.body = body
            return response
    except urllib.error.HTTPError as exc:
        response = _HTTPResponse()
        response.status_code = exc.code
        response.body = exc.read().decode("utf-8")
        return response


# ---------------------------------------------------------------------------
# Telegram Bot
# ---------------------------------------------------------------------------


@dataclass
class BotCommand:
    """Parsed bot command."""

    chat_id: str
    command: str
    args: str
    message_id: int
    is_allowed: bool


class TelegramInboundBot:
    """Telegram bot for manual URL submissions.

    Commands:
    /url <url> - Enqueue a URL for processing
    /status <queue_id> - Check task status
    /recent - Show recent tasks
    /failed - Show recent failed tasks
    /help - Show help message
    """

    def __init__(
        self,
        config: V3Config,
        *,
        queue_store: QueueStore,
        bot_token: str,
        allowed_chat_ids: list[str] | None = None,
        http_get: _HTTPGet | None = None,
        http_post: _HTTPPost | None = None,
        timeout: int = 30,
    ) -> None:
        self._config = config
        self._queue = queue_store
        self._bot_token = bot_token
        self._allowed_chat_ids = set(allowed_chat_ids or [])
        self._http_get = http_get or _default_http_get
        self._http_post = http_post or _default_http_post
        self._timeout = timeout
        self._shutdown_requested = False

        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # -- Polling loop -------------------------------------------------------

    def run_loop(
        self,
        *,
        poll_interval_seconds: int = 30,
        max_iterations: int = 0,
    ) -> int:
        """Run bot in polling loop.

        Returns count of messages processed.
        """
        offset = 0
        processed = 0
        iteration = 0

        while not self._shutdown_requested:
            if max_iterations > 0 and iteration >= max_iterations:
                break

            # Fetch updates
            updates = self._get_updates(offset)

            if updates:
                for update in updates:
                    if self._shutdown_requested:
                        break

                    command = self._parse_command(update)
                    if command and command.is_allowed:
                        self._handle_command(command)
                        processed += 1

                    # Update offset
                    offset = max(offset, update.get("update_id", 0) + 1)

            iteration += 1

            if not updates and not self._shutdown_requested:
                self._wait(poll_interval_seconds)

        return processed

    # -- Command handling ----------------------------------------------------

    def _parse_command(self, update: dict) -> BotCommand | None:
        """Parse Telegram update into command."""
        message = _message_from_update(update)
        if not message:
            return None

        chat = message.get("chat", {})
        chat_id = str(chat.get("id", ""))

        text = message.get("text") or message.get("caption") or ""
        if not text:
            return None

        # Parse command
        parts = text.split(None, 1)
        if parts and parts[0].startswith("/"):
            command = parts[0][1:]  # Remove leading /
            args = parts[1] if len(parts) > 1 else ""
        elif _extract_urls(text):
            command = "url"
            args = text
        else:
            command = "input"
            args = text.strip()
        message_id = message.get("message_id", 0)

        is_allowed = (
            not self._allowed_chat_ids or
            chat_id in self._allowed_chat_ids or
            chat_id in self._config.outputs.__dict__.values()  # Check configured chats
        )

        return BotCommand(
            chat_id=chat_id,
            command=command,
            args=args,
            message_id=message_id,
            is_allowed=is_allowed,
        )

    def _handle_command(self, cmd: BotCommand) -> None:
        """Handle a bot command."""
        if cmd.command == "url":
            self._cmd_url(cmd)
        elif cmd.command == "status":
            self._cmd_status(cmd)
        elif cmd.command == "recent":
            self._cmd_recent(cmd)
        elif cmd.command == "failed":
            self._cmd_failed(cmd)
        elif cmd.command == "help":
            self._cmd_help(cmd)
        elif cmd.command == "input":
            self._cmd_input(cmd)
        else:
            self._send_message(
                cmd.chat_id,
                f"Unknown command: /{cmd.command}\n\nUse /help for available commands.",
            )

    # -- Individual commands ------------------------------------------------

    def _cmd_url(self, cmd: BotCommand) -> None:
        """Enqueue URL from /url command."""
        urls = _submission_urls(cmd.args)

        if not urls:
            self._send_message(
                cmd.chat_id,
                "Send a URL, paste a message containing a URL, or use /url <URL>.",
            )
            return

        enqueued: list[tuple[int, str]] = []
        failures: list[str] = []

        for url in urls:
            try:
                task = self._queue.enqueue(
                    url,
                    source="telegram_bot",
                    priority=10,  # High priority for manual submissions
                    reply_channel="telegram",
                    reply_chat_id=cmd.chat_id,
                )
                enqueued.append((task.id, url))
            except Exception as exc:
                failures.append(f"{url[:100]} -> {exc}")

        if enqueued:
            if len(enqueued) == 1:
                task_id, url = enqueued[0]
                lines = [
                    f"✓ Enqueued (ID: {task_id})",
                    url[:100],
                    "",
                    "I will reply here when processing finishes or fails.",
                    f"Use /status {task_id} to check progress.",
                ]
            else:
                lines = ["✓ Enqueued URLs:"]
                lines.extend(f"- ID {task_id}: {url[:100]}" for task_id, url in enqueued)
                lines.extend([
                    "",
                    "I will reply here as each item finishes or fails.",
                ])
            if failures:
                lines.extend(["", "Some URLs failed:"])
                lines.extend(f"- {failure}" for failure in failures[:5])
            self._send_message(cmd.chat_id, "\n".join(lines))
            return

        self._send_message(
            cmd.chat_id,
            "Failed to enqueue:\n" + "\n".join(f"- {failure}" for failure in failures[:5]),
        )

    def _cmd_input(self, cmd: BotCommand) -> None:
        """Acknowledge plain text input instead of silently dropping it."""
        self._send_message(
            cmd.chat_id,
            (
                "Text input is active. For extraction, send a URL directly or paste "
                "a message that contains one."
            )
        )

    def _cmd_status(self, cmd: BotCommand) -> None:
        """Show task status from /status command."""
        task_id_str = cmd.args.strip()

        if not task_id_str:
            self._send_message(cmd.chat_id, "Usage: /status <queue_id>")
            return

        try:
            task_id = int(task_id_str)
            task = self._queue.get_task(task_id)

            status_lines = [
                f"Task {task.id}:",
                f"URL: {task.url[:100]}",
                f"Status: {task.status.value}",
                f"Attempt: {task.attempt_count}/{task.max_attempts}",
                f"Updated: {task.updated_at}",
                f"Created: {task.created_at}",
            ]

            # Show reply info
            if task.reply_channel or task.reply_chat_id:
                status_lines.append(
                    f"Reply: {task.reply_channel or '-'} {task.reply_chat_id or '-'}"
                )

            # Show lease/diagnostics info
            if task.processing_owner:
                status_lines.append(f"Processing by: {task.processing_owner}")
            if task.processing_started_at:
                status_lines.append(f"Started at: {task.processing_started_at}")
            if task.processing_heartbeat_at:
                status_lines.append(f"Heartbeat: {task.processing_heartbeat_at}")
            if task.provider_route:
                status_lines.append(f"Provider: {task.provider_route}")
            if task.last_reply_status:
                status_lines.append(f"Last reply: {task.last_reply_status}")

            # Show failure details
            if task.status.value in ("retry_scheduled", "rejected", "failed_terminal"):
                if task.failure_kind.value:
                    status_lines.append(f"Failure: {task.failure_kind.value}")
                status_lines.append(f"Error: {task.last_error[:200]}")
                if task.next_action.value:
                    status_lines.append(f"Next action: {task.next_action.value}")
                if task.next_retry_at:
                    status_lines.append(f"Retry at: {task.next_retry_at}")

            # Show output path
            if task.output_path:
                status_lines.append(f"Output: {task.output_path}")

            # Check for stuck processing
            if task.status.value == "processing":
                from datetime import datetime, UTC, timedelta
                try:
                    updated = datetime.fromisoformat(task.updated_at)
                    if datetime.now(UTC) - updated > timedelta(minutes=30):
                        status_lines.append("⚠️ Task appears stuck (processing > 30min)")
                except ValueError:
                    pass

            self._send_message(cmd.chat_id, "\n".join(status_lines))
        except (ValueError, KeyError):
            self._send_message(cmd.chat_id, f"Task not found: {task_id_str}")
        except Exception as exc:
            self._send_message(cmd.chat_id, f"Error: {exc}")

    def _cmd_recent(self, cmd: BotCommand) -> None:
        """Show recent tasks from /recent command."""
        # Get pending tasks
        counts = self._queue.count_by_status()
        pending = counts.get("pending", 0)
        processing = counts.get("processing", 0)
        retry = counts.get("retry_scheduled", 0)

        lines = [
            "Queue Summary:",
            f"Pending: {pending}",
            f"Processing: {processing}",
            f"Retry Scheduled: {retry}",
        ]

        self._send_message(cmd.chat_id, "\n".join(lines))

    def _cmd_failed(self, cmd: BotCommand) -> None:
        """Show failed tasks from /failed command."""
        # For simplicity, just show count
        counts = self._queue.count_by_status()
        failed = counts.get("failed_terminal", 0)

        self._send_message(
            cmd.chat_id,
            f"Failed tasks: {failed}\n\nUse /status <id> for details.",
        )

    def _cmd_help(self, cmd: BotCommand) -> None:
        """Show help from /help command."""
        help_text = (
            "*V3 Knowledge Extractor Bot*\n\n"
            "Commands:\n"
            "/url <url> - Enqueue a URL for processing\n"
            "Send or paste text containing a URL - Enqueue it for processing\n"
            "/status <id> - Check task status\n"
            "/recent - Show queue summary\n"
            "/failed - Show failed tasks count\n"
            "/help - Show this message"
        )
        self._send_message(cmd.chat_id, help_text)

    # -- Telegram API methods -----------------------------------------------

    def _get_updates(self, offset: int = 0) -> list[dict]:
        """Fetch updates from Telegram."""
        url = f"https://api.telegram.org/bot{self._bot_token}/getUpdates"
        if offset > 0:
            url += f"?offset={offset}"

        try:
            response = self._http_get(url, timeout=self._timeout)
        except Exception:
            return []

        if response.status_code != 200:
            return []

        try:
            data = json.loads(response.body)
            return data.get("result", [])
        except json.JSONDecodeError:
            return []

    def _send_message(self, chat_id: str, text: str) -> None:
        """Send message to Telegram chat."""
        url = f"https://api.telegram.org/bot{self._bot_token}/sendMessage"

        payload = {
            "chat_id": chat_id,
            "text": text,
        }

        try:
            response = self._http_post(
                url,
                data=json.dumps(payload).encode("utf-8"),
                timeout=self._timeout,
            )
        except Exception:
            return

        # Log error but don't fail
        if response.status_code != 200:
            pass

    # -- Signal handling -----------------------------------------------------

    def _handle_shutdown(self, signum: int, frame) -> None:  # type: ignore
        """Handle shutdown signals."""
        self._shutdown_requested = True

    def _wait(self, seconds: int) -> None:
        """Wait for signal or timeout."""
        if seconds <= 0:
            return

        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._shutdown_requested:
            remaining = deadline - time.monotonic()
            try:
                time.sleep(min(1.0, max(0.0, remaining)))
            except KeyboardInterrupt:
                self._shutdown_requested = True
                break


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def create_bot(
    config: V3Config,
    *,
    queue_store: QueueStore | None = None,
    bot_token: str = "",
    allowed_chat_ids: list[str] | None = None,
) -> TelegramInboundBot:
    """Create TelegramInboundBot from V3Config."""
    if queue_store is None:
        from pathlib import Path
        loader = ConfigLoader(project_root=Path.cwd())
        queue_path = loader.expand_path(config.runtime.queue_db_path)
        queue_store = QueueStore(queue_path)

    if not bot_token:
        # Use token from config or env if not provided
        import os
        bot_token = config.outputs.telegram_bot_token or os.environ.get(config.outputs.telegram_bot_token_env, "")

    return TelegramInboundBot(
        config=config,
        queue_store=queue_store,
        bot_token=bot_token,
        allowed_chat_ids=allowed_chat_ids,
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for Telegram bot.

    Usage:
        python -m knowledge_extractor_v3.telegram_bot [--poll N] [--max-iter N]
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(description="V3 Telegram Inbound Bot")
    parser.add_argument(
        "--poll",
        type=int,
        default=30,
        help="Poll interval in seconds (default: 30)",
    )
    parser.add_argument(
        "--max-iter",
        type=int,
        default=0,
        help="Maximum iterations (default: unlimited)",
    )

    args = parser.parse_args(argv)

    # Load config
    project_root = Path(__file__).resolve().parents[2]
    loader = ConfigLoader(project_root=project_root)
    config = loader.load()

    # Check if bot is enabled
    if not config.telegram_bot.enabled:
        print("Telegram bot is not enabled in config", file=sys.stderr)
        return 1

    # Resolve runtime paths using unified function
    paths = resolve_runtime_paths(project_root, config, loader, env=os.environ)

    # Runtime guard
    guard = RuntimeGuard(paths)
    try:
        guard.validate(write_fingerprint=False)
    except RuntimeGuardError as exc:
        print(f"Runtime guard check failed: {exc}", file=sys.stderr)
        return 1

    # Get bot token
    token_env = config.outputs.telegram_bot_token_env
    bot_token = os.environ.get(token_env, "")
    if not bot_token:
        print(f"Bot token not found in environment: {token_env}", file=sys.stderr)
        return 1

    # Get allowed chat IDs
    allowed_env = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
    allowed_chat_ids = allowed_env.split(",") if allowed_env else None

    # Create bot with unified paths
    queue_store = QueueStore(paths.queue_db_path)
    bot = create_bot(
        config,
        queue_store=queue_store,
        bot_token=bot_token,
        allowed_chat_ids=allowed_chat_ids,
    )

    # Run
    print("Starting Telegram bot polling...")
    processed = bot.run_loop(poll_interval_seconds=args.poll, max_iterations=args.max_iter)
    print(f"Processed {processed} messages")

    return 0


def _looks_like_url(text: str) -> bool:
    return bool(_submission_urls(text))


def _message_from_update(update: dict) -> dict:
    """Return the Telegram message-like payload we can process."""
    for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
        message = update.get(key, {})
        if message:
            return message
    return {}


_URL_RE = re.compile(r"(?i)\b((?:https?://|www\.)[^\s<>()\"']+)")
_TRAILING_URL_PUNCTUATION = ".,;:!?)]}>\"'，。；：！？）】》"


def _extract_urls(text: str) -> list[str]:
    """Extract http(s)/www URLs from pasted Telegram text."""
    urls: list[str] = []
    seen: set[str] = set()
    for match in _URL_RE.finditer(text):
        url = _normalize_submission_url(match.group(1))
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _submission_urls(text: str) -> list[str]:
    """Return URL submissions supported by the bot."""
    urls = _extract_urls(text)
    if urls:
        return urls

    candidate = text.strip()
    if not candidate:
        return []

    if candidate.startswith("fixture://"):
        return [candidate]

    if re.match(r"(?i)^(?:https?://|www\.)\S+\.\S+$", candidate):
        return [_normalize_submission_url(candidate)]

    # Allow a bare domain only when it is a single token; prose with dots should
    # be acknowledged as text input instead of becoming a broken queue item.
    if " " not in candidate and re.match(r"(?i)^[a-z0-9.-]+\.[a-z]{2,}(?:/[^\s]*)?$", candidate):
        return [_normalize_submission_url(candidate)]

    return []


def _normalize_submission_url(url: str) -> str:
    normalized = url.strip().rstrip(_TRAILING_URL_PUNCTUATION)
    if normalized.lower().startswith("www."):
        return f"https://{normalized}"
    return normalized


if __name__ == "__main__":
    sys.exit(main())
