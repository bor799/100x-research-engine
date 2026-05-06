"""Live Telegram client for real message delivery via Bot API.

In production, makes HTTP POST to api.telegram.org.
In tests, callers provide http_post= to intercept requests.
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Callable, Protocol

from ..models import FetchedContent, TypedError
from ..queue_store import FailureKind, NextAction


class _HTTPResponse:
    """Minimal response interface for test mocks."""

    status_code: int
    body: str


class _HTTPPost(Protocol):
    def __call__(
        self,
        url: str,
        *,
        data: bytes,
        timeout: int,
    ) -> _HTTPResponse: ...


def _default_http_post(
    url: str,
    *,
    data: bytes,
    timeout: int,
) -> _HTTPResponse:
    """Real HTTP POST using urllib (stdlib)."""
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


class LiveTelegramClient:
    """Real Telegram delivery via Bot API sendMessage.

    The http_post parameter accepts a callable for testing.
    Production uses urllib.request (stdlib, no external HTTP dependency).
    """

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
        max_length: int = 4096,
        http_post: _HTTPPost | None = None,
        timeout: int = 30,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self.max_length = max_length
        self._http_post = http_post or _default_http_post
        self._timeout = timeout

    def deliver(
        self,
        content: FetchedContent,
        text: str,
        *,
        chat_id: str | None = None,
    ) -> tuple[str, str] | TypedError:
        """Send brief to Telegram. Returns (status, preview) or TypedError."""
        if not self.enabled:
            preview = text.strip()[:80]
            return "disabled", preview

        payload = {
            "chat_id": chat_id or self.chat_id,
            "text": text.strip()[:self.max_length],
        }
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

        try:
            response = self._http_post(
                url,
                data=json.dumps(payload).encode("utf-8"),
                timeout=self._timeout,
            )
        except Exception as exc:
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Telegram HTTP request failed",
                stage="output.telegram",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                detail=str(exc),
            )

        if response.status_code != 200:
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message=f"Telegram API returned HTTP {response.status_code}",
                stage="output.telegram",
                retryable=response.status_code >= 500,
                next_action=NextAction.RETRY_LATER if response.status_code >= 500 else NextAction.MANUAL_REVIEW,
                detail=response.body[:200],
            )

        preview = text.strip()[:80]
        return "sent", preview
