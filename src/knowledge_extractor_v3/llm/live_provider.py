"""Real LLM provider with environment-based routing and retry mapping.

Supports zhipu, anthropic, openai routing based on config.provider.
Maps HTTP errors to FailureKind and NextAction for queue retry logic.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.request
import urllib.error
from typing import Callable, Protocol

from ..models import (
    ExtractionResult,
    FetchedContent,
    ScoreResult,
    TypedError,
    retry_at,
    sha256_text,
)
from ..queue_store import FailureKind, NextAction


# ---------------------------------------------------------------------------
# HTTP abstraction for testability
# ---------------------------------------------------------------------------


class _HTTPResponse:
    """Minimal response interface for test mocks."""

    status_code: int
    body: str


class _HTTPPost(Protocol):
    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes,
        timeout: int,
    ) -> _HTTPResponse: ...


def _default_http_post(
    url: str,
    *,
    headers: dict[str, str],
    data: bytes,
    timeout: int,
) -> _HTTPResponse:
    """Real HTTP POST using urllib (stdlib)."""
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
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
    except urllib.error.URLError as exc:
        # Timeout or connection error
        if isinstance(exc.reason, TimeoutError) or "timeout" in str(exc.reason).lower():
            response = _HTTPResponse()
            response.status_code = 408  # Request Timeout
            response.body = str(exc.reason)
            return response
        response = _HTTPResponse()
        response.status_code = 503  # Service Unavailable
        response.body = str(exc.reason)
        return response
    except Exception as exc:
        response = _HTTPResponse()
        response.status_code = 500
        response.body = str(exc)
        return response


# ---------------------------------------------------------------------------
# Provider configuration
# ---------------------------------------------------------------------------


class LiveLLMConfig:
    """Configuration for real LLM provider."""

    def __init__(
        self,
        *,
        provider: str = "zhipu",
        api_key_env: str = "ZHIPU_API_KEY",
        scoring_model: str = "",
        extraction_model: str = "",
        request_timeout_seconds: int = 90,
        max_retries: int = 2,
        min_delay_seconds: float = 2.0,
    ) -> None:
        self.provider = provider
        self.api_key_env = api_key_env
        self.scoring_model = scoring_model
        self.extraction_model = extraction_model
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.min_delay_seconds = min_delay_seconds


# ---------------------------------------------------------------------------
# Endpoint builders per provider
# ---------------------------------------------------------------------------


def _build_zhipu_request(
    content: str,
    prompt: str,
    model: str,
    api_key: str,
) -> tuple[str, dict[str, str], bytes]:
    """Build Zhipu API request.

    Zhipu uses JWT token generation, but for simplicity we use
    the API key directly in the Authorization header.
    """
    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt + "\n\n" + content},
        ],
        "temperature": 0.3,
    }

    return url, headers, json.dumps(payload).encode("utf-8")


def _build_anthropic_request(
    content: str,
    prompt: str,
    model: str,
    api_key: str,
) -> tuple[str, dict[str, str], bytes]:
    """Build Anthropic Claude API request."""
    url = "https://api.anthropic.com/v1/messages"

    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
        "anthropic-version": "2023-06-01",
    }

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {"role": "user", "content": prompt + "\n\n" + content},
        ],
        "temperature": 0.3,
    }

    return url, headers, json.dumps(payload).encode("utf-8")


def _build_openai_request(
    content: str,
    prompt: str,
    model: str,
    api_key: str,
) -> tuple[str, dict[str, str], bytes]:
    """Build OpenAI API request."""
    url = "https://api.openai.com/v1/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt + "\n\n" + content},
        ],
        "temperature": 0.3,
    }

    return url, headers, json.dumps(payload).encode("utf-8")


# Default models per provider
_DEFAULT_MODELS = {
    "zhipu": "glm-4-flash",
    "anthropic": "claude-3-5-sonnet-20241022",
    "openai": "gpt-4o-mini",
}

_REQUEST_BUILDERS = {
    "zhipu": _build_zhipu_request,
    "anthropic": _build_anthropic_request,
    "openai": _build_openai_request,
}


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


def _map_http_error(
    status_code: int,
    body: str,
    stage: str,
) -> tuple[FailureKind, NextAction, bool]:
    """Map HTTP status code to FailureKind and NextAction.

    Returns (failure_kind, next_action, retryable).
    """
    body_lower = body.lower()

    # Rate limiting
    if (
        status_code == 429
        or "rate_limit" in body_lower
        or "rate limit" in body_lower
        or "1308" in body_lower
        or "1302" in body_lower
    ):
        return FailureKind.LLM_RATE_LIMIT, NextAction.RETRY_LATER, True

    # Timeout
    if status_code == 408 or "timeout" in body_lower:
        return FailureKind.LLM_TIMEOUT, NextAction.RETRY_LATER, True

    # Auth errors - not retryable without credential refresh
    if status_code in (401, 403) or "unauthorized" in body_lower or "forbidden" in body_lower:
        return FailureKind.AUTH_INVALID, NextAction.MANUAL_REVIEW, False

    # Server errors - retryable
    if status_code >= 500:
        return FailureKind.LLM_TIMEOUT, NextAction.RETRY_LATER, True

    # Client errors (except auth) - terminal
    if status_code >= 400:
        return FailureKind.PARSE_ERROR, NextAction.MANUAL_REVIEW, False

    # Network/unknown errors - retryable
    return FailureKind.LLM_TIMEOUT, NextAction.RETRY_LATER, True


def _retry_after_seconds(body: str) -> float:
    body_lower = body.lower()
    for key in ("retry_after", "retry-after", "retry after"):
        if key in body_lower:
            match = re.search(rf"{re.escape(key)}[\"'\s:=]+(\d+)", body_lower)
            if match:
                return float(match.group(1))
    return 0.0


def _retry_after_minutes(body: str) -> int:
    seconds = _retry_after_seconds(body)
    if seconds <= 0:
        return 5
    return max(1, int((seconds + 59) // 60))


def _stringify(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _format_score(value: object) -> str:
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _numbered_lines(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    if not items:
        return ["1. unavailable"]
    return [f"{index}. {_stringify(item)}" for index, item in enumerate(items, start=1)]


def _bullet_lines(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    if not items:
        return ["- unavailable"]
    return [f"- {_stringify(item)}" for item in items]


def _evidence_lines(value: object) -> list[str]:
    items = value if isinstance(value, list) else []
    if not items:
        return ["- unavailable"]

    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            evidence_id = item.get("id") or f"E{index}"
            claim = item.get("claim") or ""
            provenance = item.get("provenance") or item.get("source") or ""
            suffix = f" ({provenance})" if provenance else ""
            lines.append(f"- {evidence_id}: {claim}{suffix}".strip())
        else:
            lines.append(f"- {_stringify(item)}")
    return lines


def _build_extraction_input(content: FetchedContent, score: ScoreResult) -> str:
    """Give extraction prompts the scoring context they are expected to honor."""
    score_payload = getattr(score, "parsed", None)
    if not isinstance(score_payload, dict):
        score_payload = {
            "score": getattr(score, "score", ""),
            "final_score": getattr(score, "final_score", ""),
            "signal_tier": getattr(score, "signal_tier", ""),
            "decision_window_status": getattr(score, "decision_window_status", ""),
            "source_type": getattr(score, "source_type", ""),
            "source_tier": getattr(score, "source_tier", ""),
            "interest_flag": getattr(score, "interest_flag", ""),
            "attribution_chain": getattr(score, "attribution_chain", ""),
        }

    content_payload = {
        "url": content.url,
        "source": content.source,
        "source_type": content.source_type,
        "title": content.title,
        "author": content.author,
        "published_at": content.published_at,
        "fetched_at": content.fetched_at,
        "content_hash": content.content_hash,
        "metadata": content.metadata,
    }

    return "\n\n".join(
        [
            "SCORING_CONTEXT_JSON:",
            json.dumps(score_payload, ensure_ascii=False, sort_keys=True),
            "CONTENT_METADATA_JSON:",
            json.dumps(content_payload, ensure_ascii=False, sort_keys=True),
            "SOURCE_TEXT:",
            content.text,
        ]
    )


# ---------------------------------------------------------------------------
# LiveLLMProvider
# ---------------------------------------------------------------------------


class LiveLLMProvider:
    """Real LLM provider with configurable routing and retry semantics.

    Provider selection and API keys come from config and environment.
    HTTP errors are mapped to queue FailureKind for proper retry logic.
    """

    model_route = "live://provider"

    def __init__(
        self,
        config: LiveLLMConfig,
        *,
        env: dict[str, str] | None = None,
        http_post: _HTTPPost | None = None,
    ) -> None:
        self._config = config
        self._env = env or os.environ
        self._http_post = http_post or _default_http_post
        self._circuit_open_until = 0.0

    @property
    def model_route(self) -> str:
        return f"live://{self._config.provider}"

    def score(self, content: FetchedContent, prompt: str) -> str | TypedError:
        """Run scoring prompt against configured LLM."""
        model = self._config.scoring_model or _DEFAULT_MODELS.get(self._config.provider, "gpt-4o-mini")
        raw = self._call_llm(content.text, prompt, model, stage="score")

        return raw

    def extract(
        self,
        content: FetchedContent,
        score: ScoreResult,
        prompt: str,
    ) -> str | TypedError:
        """Run extraction prompt against configured LLM."""
        model = self._config.extraction_model or _DEFAULT_MODELS.get(self._config.provider, "gpt-4o-mini")
        raw = self._call_llm(_build_extraction_input(content, score), prompt, model, stage="extract")

        return raw

    def format_telegram(
        self,
        score: ScoreResult,
        extraction: ExtractionResult,
        prompt: str,
        *,
        content: FetchedContent | None = None,
    ) -> str | TypedError:
        """Format telegram brief from the V3 primary-market extraction schema."""
        title = extraction.title or "Untitled"
        one_liner = extraction.one_line_signal or ""
        parsed = extraction.parsed
        link = (
            content.url if content is not None else
            str(parsed.get("original_url") or parsed.get("url") or score.parsed.get("url") or "")
        )
        final_score = getattr(score, "final_score", "")
        score_value = getattr(score, "score", "")
        decision_window = getattr(score, "decision_window_status", "")
        source_type = getattr(score, "source_type", "")
        source_tier = getattr(score, "source_tier", "")
        interest_flag = getattr(score, "interest_flag", "")
        attribution_chain = getattr(score, "attribution_chain", "")

        lines = [
            f"[{score.signal_tier}] {title}",
            "",
            f"Score: {_format_score(final_score)} / {_format_score(score_value)}",
            f"Window: {parsed.get('decision_window_status') or decision_window}",
            f"Source: {parsed.get('source_type') or source_type} / {parsed.get('source_tier') or source_tier}",
            f"Interest: {parsed.get('interest_flag') or interest_flag}",
            "",
            "Signal:",
            one_liner,
            "",
            "Why it matters:",
            *_numbered_lines(parsed.get("why_it_matters")),
            "",
            "Evidence:",
            *_evidence_lines(parsed.get("evidence")),
            "",
            "Action:",
            *_bullet_lines(parsed.get("recommended_actions")),
            "",
            "Attribution:",
            _stringify(parsed.get("attribution_chain") or attribution_chain),
            "",
            "Link:",
            link,
        ]
        return "\n".join(line for line in lines if line is not None).strip()

    def _call_llm(
        self,
        content: str,
        prompt: str,
        model: str,
        *,
        stage: str = "llm_call",
    ) -> str | TypedError:
        """Make LLM API call with error mapping."""
        api_key = self._env.get(self._config.api_key_env, "")
        if not api_key:
            return TypedError(
                failure_kind=FailureKind.AUTH_INVALID,
                message=f"API key not found in environment: {self._config.api_key_env}",
                stage=stage,
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail="Configure the API key in environment or config.local.yaml",
            )

        builder = _REQUEST_BUILDERS.get(self._config.provider, _build_openai_request)
        url, headers, data = builder(content, prompt, model, api_key)

        if time.time() < self._circuit_open_until:
            return TypedError(
                failure_kind=FailureKind.LLM_RATE_LIMIT,
                message="LLM circuit breaker is open",
                stage=stage,
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                next_retry_at=retry_at(5),
            )

        attempts = max(1, self._config.max_retries + 1)
        last_error: TypedError | None = None
        response: _HTTPResponse | None = None

        for attempt in range(attempts):
            if attempt > 0:
                time.sleep(self._retry_delay(attempt, last_error))

            response = self._http_post(
                url,
                headers=headers,
                data=data,
                timeout=self._config.request_timeout_seconds,
            )

            if response.status_code == 200:
                last_error = None
                break

            failure_kind, next_action, retryable = _map_http_error(
                response.status_code,
                response.body,
                stage,
            )
            last_error = TypedError(
                failure_kind=failure_kind,
                message=f"LLM API returned HTTP {response.status_code}",
                stage=stage,
                retryable=retryable,
                next_action=next_action,
                detail=response.body[:500],
                next_retry_at=retry_at(_retry_after_minutes(response.body)),
            )
            if failure_kind is FailureKind.LLM_RATE_LIMIT:
                self._circuit_open_until = time.time() + (_retry_after_minutes(response.body) * 60)
            if not retryable:
                return last_error

        if last_error is not None:
            return last_error

        assert response is not None

        # Handle HTTP errors
        if response.status_code != 200:
            failure_kind, next_action, retryable = _map_http_error(
                response.status_code,
                response.body,
                stage,
            )
            return TypedError(
                failure_kind=failure_kind,
                message=f"LLM API returned HTTP {response.status_code}",
                stage=stage,
                retryable=retryable,
                next_action=next_action,
                detail=response.body[:500],
            )

        # Extract response text based on provider
        text = self._extract_response_text(response.body)
        if not text:
            return TypedError(
                failure_kind=FailureKind.PARSE_ERROR,
                message="LLM returned empty response",
                stage=stage,
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=response.body[:200],
            )

        return text

    def _retry_delay(self, attempt: int, error: TypedError | None) -> float:
        if self._http_post is not _default_http_post:
            return 0.0
        retry_after = _retry_after_seconds(error.detail if error else "")
        if retry_after > 0:
            return min(retry_after, 60.0)
        base = max(0.0, self._config.min_delay_seconds)
        return min(base * (2 ** (attempt - 1)), 30.0)

    def _extract_response_text(self, body: str) -> str:
        """Extract actual LLM response text from provider-specific JSON."""
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            # Not JSON - return as-is
            return body

        # Zhipu/OpenAI format
        if "choices" in data and data["choices"]:
            choice = data["choices"][0]
            if "message" in choice:
                msg = choice["message"]
                if isinstance(msg, dict) and "content" in msg:
                    return str(msg["content"])

        # Anthropic format
        if "content" in data and isinstance(data["content"], list):
            blocks = data["content"]
            for block in blocks:
                if isinstance(block, dict) and block.get("type") == "text":
                    return str(block.get("text", ""))

        # Check for empty or non-matching JSON - return empty string to trigger error
        if not data or (isinstance(data, dict) and len(data) == 0):
            return ""

        # Fallback: return entire body for non-empty content
        return body


# ---------------------------------------------------------------------------
# Helper for creating provider from V3Config
# ---------------------------------------------------------------------------


def create_live_provider(
    llm_config,  # LLMConfig from config_loader
    *,
    env: dict[str, str] | None = None,
    http_post: _HTTPPost | None = None,
) -> LiveLLMProvider:
    """Create LiveLLMProvider from V3Config.llm section."""
    config = LiveLLMConfig(
        provider=llm_config.provider,
        api_key_env=llm_config.api_key_env,
        scoring_model=llm_config.scoring_model,
        extraction_model=llm_config.extraction_model,
        request_timeout_seconds=llm_config.request_timeout_seconds,
        max_retries=llm_config.max_retries,
        min_delay_seconds=llm_config.min_delay_seconds,
    )
    return LiveLLMProvider(config, env=env, http_post=http_post)
