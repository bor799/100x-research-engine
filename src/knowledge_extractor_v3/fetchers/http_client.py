"""Unified HTTP client for all fetchers.

Provides:
- User-Agent headers
- Proxy support
- Retry logic
- Jina Reader fallback for 403 errors
- Consistent error handling
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..models import TypedError
from ..queue_store import FailureKind, NextAction

if TYPE_CHECKING:
    from collections.abc import Callable


DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36 "
    "100x-knowledge-extractor-v3/1.0"
)

JINA_READER_URL = "https://r.jina.ai/"

DEFAULT_TIMEOUT = 30
DEFAULT_MAX_RETRIES = 3


@dataclass
class HttpClientResponse:
    """Response from HttpClient."""

    status: int
    content: str
    content_type: str
    url: str
    via_jina: bool = False

    @property
    def is_success(self) -> bool:
        return 200 <= self.status < 300

    @property
    def is_forbidden(self) -> bool:
        return self.status == 403

    @property
    def is_not_found(self) -> bool:
        return self.status == 404


class HttpClient:
    """Unified HTTP client with retry and Jina fallback.

    Usage:
        client = HttpClient()
        response = client.get("https://example.com/feed.xml")
        if response.is_success:
            print(response.content)
    """

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        proxy: str | None = None,
        enable_jina_fallback: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.proxy = proxy
        self.enable_jina_fallback = enable_jina_fallback

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> HttpClientResponse | TypedError:
        """Send GET request with retry logic.

        Args:
            url: URL to fetch
            headers: Additional headers (User-Agent is always set)
            timeout: Override default timeout

        Returns:
            HttpClientResponse on success, TypedError on failure
        """
        timeout = timeout or self.timeout
        last_error: Exception | None = None

        # Try direct request with retries
        for attempt in range(self.max_retries):
            if attempt > 0:
                # Exponential backoff
                sleep_time = min(2 ** (attempt - 1), 10)
                time.sleep(sleep_time)

            result = self._fetch_once(url, headers=headers, timeout=timeout)

            if isinstance(result, TypedError):
                last_error = result
                # Don't retry on validation errors or auth errors
                if not result.retryable:
                    return result
                # Don't retry on 404
                if "404" in result.message:
                    return result
                continue

            # Success - check for 403 which should trigger Jina fallback
            if result.is_forbidden and self.enable_jina_fallback:
                jina_result = self._get_via_jina(url)
                if isinstance(jina_result, HttpClientResponse):
                    return jina_result
                # Jina failed, return original 403
                return result

            return result

        # All retries exhausted
        if isinstance(last_error, TypedError):
            return last_error

        return TypedError(
            failure_kind=FailureKind.FETCH_FAILED,
            message="HTTP request failed after retries",
            stage="fetch",
            retryable=True,
            next_action=NextAction.RETRY_LATER,
            detail=url,
        )

    def get_via_jina(self, url: str) -> HttpClientResponse | TypedError:
        """Fetch content via Jina Reader (bypasses 403).

        Jina Reader: https://r.jina.ai/
        Converts any URL to readable markdown/text.
        """
        return self._get_via_jina(url)

    def _fetch_once(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: int,
    ) -> HttpClientResponse | TypedError:
        """Single fetch attempt."""
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml,text/plain;q=0.9,*/*;q=0.1",
        }
        if headers:
            request_headers.update(headers)

        request = urllib.request.Request(url, headers=request_headers)

        try:
            with self._urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                content = response.read().decode("utf-8", errors="replace")
                return HttpClientResponse(
                    status=getattr(response, "status", response.getcode()),
                    content=content,
                    content_type=content_type,
                    url=url,
                    via_jina=False,
                )
        except urllib.error.HTTPError as exc:
            return HttpClientResponse(
                status=exc.code,
                content=exc.read().decode("utf-8", errors="replace") if exc.fp else "",
                content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
                url=url,
                via_jina=False,
            )
        except urllib.error.URLError as exc:
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message=f"URL fetch failed: {exc.reason}",
                stage="fetch",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                detail=url,
            )
        except TimeoutError:
            return TypedError(
                failure_kind=FailureKind.FETCH_TIMEOUT,
                message="Request timed out",
                stage="fetch",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                detail=url,
            )
        except OSError as exc:
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message=f"Network error: {exc}",
                stage="fetch",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                detail=url,
            )

    def _get_via_jina(self, url: str) -> HttpClientResponse | TypedError:
        """Fetch via Jina Reader."""
        jina_url = f"{JINA_READER_URL}{url}"

        try:
            # Jina Reader requires User-Agent header
            request = urllib.request.Request(jina_url, headers={"User-Agent": self.user_agent})
            with self._urlopen(request, timeout=self.timeout + 10) as response:
                content_type = response.headers.get("Content-Type", "")
                content = response.read().decode("utf-8", errors="replace")
                status = getattr(response, "status", response.getcode())

                if status >= 400:
                    return TypedError(
                        failure_kind=FailureKind.FETCH_FAILED,
                        message=f"Jina Reader returned HTTP {status}",
                        stage="fetch",
                        retryable=False,
                        next_action=NextAction.MANUAL_REVIEW,
                        detail=f"jina_url={jina_url}",
                    )

                return HttpClientResponse(
                    status=status,
                    content=content,
                    content_type=content_type,
                    url=url,
                    via_jina=True,
                )
        except Exception as exc:
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message=f"Jina Reader fallback failed: {exc}",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=f"url={url} jina_error={exc}",
            )

    def _urlopen(self, request: urllib.request.Request, *, timeout: int):
        if not self.proxy:
            return urllib.request.urlopen(request, timeout=timeout)

        proxy_handler = urllib.request.ProxyHandler({
            "http": self.proxy,
            "https": self.proxy,
        })
        opener = urllib.request.build_opener(proxy_handler)
        return opener.open(request, timeout=timeout)


def create_http_client(
    *,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
    proxy: str | None = None,
) -> HttpClient:
    """Factory function to create HttpClient with defaults."""
    return HttpClient(
        user_agent=user_agent,
        timeout=timeout,
        max_retries=max_retries,
        proxy=proxy,
    )
