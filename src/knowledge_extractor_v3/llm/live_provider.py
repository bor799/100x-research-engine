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
        api_key: str = "",  # 直接配置的 API key（优先于环境变量）
        api_base: str = "",
        scoring_model: str = "",
        extraction_model: str = "",
        telegram_model: str = "",
        request_timeout_seconds: int = 90,
        max_retries: int = 2,
        min_delay_seconds: float = 2.0,
        temperature: float = 0.1,
        fallback_providers: list[dict] | None = None,
    ) -> None:
        self.provider = provider
        self.api_key_env = api_key_env
        self.api_key = api_key
        self.api_base = api_base
        self.scoring_model = scoring_model
        self.extraction_model = extraction_model
        self.telegram_model = telegram_model
        self.request_timeout_seconds = request_timeout_seconds
        self.max_retries = max_retries
        self.min_delay_seconds = min_delay_seconds
        self.temperature = temperature
        self.fallback_providers = fallback_providers or []

    def all_provider_configs(self) -> list[dict]:
        """Return list of all provider configs in order: primary, then fallbacks."""
        configs = [{
            "provider": self.provider,
            "api_key_env": self.api_key_env,
            "api_key": self.api_key,
            "api_base": self.api_base,
            "scoring_model": self.scoring_model,
            "extraction_model": self.extraction_model,
            "telegram_model": self.telegram_model,
            "temperature": self.temperature,
        }]
        for fb in self.fallback_providers:
            configs.append({
                "provider": fb.get("provider", "openai"),
                "api_key_env": fb.get("api_key_env", "OPENAI_API_KEY"),
                "api_key": fb.get("api_key", ""),
                "api_base": fb.get("api_base", ""),
                "scoring_model": fb.get("scoring_model", ""),
                "extraction_model": fb.get("extraction_model", ""),
                "telegram_model": fb.get("telegram_model", fb.get("telegram_brief_model", "")),
                "temperature": fb.get("temperature", self.temperature),
            })
        return configs


# ---------------------------------------------------------------------------
# Endpoint builders per provider
# ---------------------------------------------------------------------------


_DEFAULT_API_BASES = {
    "zhipu": "https://open.bigmodel.cn/api/coding/paas/v4",
    "anthropic": "https://api.anthropic.com/v1",
    "openai": "https://api.openai.com/v1",
}


def _endpoint_url(
    api_base: str,
    *,
    provider: str,
    endpoint: str,
    configured_url_is_endpoint: bool = False,
) -> str:
    base = (api_base or _DEFAULT_API_BASES[provider]).rstrip("/")
    if configured_url_is_endpoint:
        return base
    if base.endswith(f"/{endpoint}"):
        return base
    return f"{base}/{endpoint}"


def _build_zhipu_request(
    content: str,
    prompt: str,
    model: str,
    api_key: str,
    api_base: str = "",
    temperature: float = 0.1,
) -> tuple[str, dict[str, str], bytes]:
    """Build Zhipu API request.

    Zhipu uses JWT token generation, but for simplicity we use
    the API key directly in the Authorization header.
    """
    url = _endpoint_url(
        api_base,
        provider="zhipu",
        endpoint="chat/completions",
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt + "\n\n" + content},
        ],
        "temperature": temperature,
    }

    return url, headers, json.dumps(payload).encode("utf-8")


def _build_anthropic_request(
    content: str,
    prompt: str,
    model: str,
    api_key: str,
    api_base: str = "",
    temperature: float = 0.1,
) -> tuple[str, dict[str, str], bytes]:
    """Build Anthropic Claude API request."""
    url = _endpoint_url(api_base, provider="anthropic", endpoint="messages")

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
        "temperature": temperature,
    }

    return url, headers, json.dumps(payload).encode("utf-8")


def _build_openai_request(
    content: str,
    prompt: str,
    model: str,
    api_key: str,
    api_base: str = "",
    temperature: float = 0.1,
) -> tuple[str, dict[str, str], bytes]:
    """Build OpenAI API request."""
    url = _endpoint_url(api_base, provider="openai", endpoint="chat/completions")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt + "\n\n" + content},
        ],
        "temperature": temperature,
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

    # Quota exhausted / balance insufficient - should trigger fallback
    if (
        "quota" in body_lower
        or "balance" in body_lower
        or "insufficient" in body_lower
        or "402" in body_lower
        or "余额不足" in body_lower
        or "配额" in body_lower
    ):
        return FailureKind.LLM_QUOTA_EXHAUSTED, NextAction.RETRY_LATER, True

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


def _content_metadata(content: FetchedContent) -> dict[str, object]:
    return {
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


def _build_scoring_input(content: FetchedContent) -> str:
    """Give scoring prompts source/title/url context, matching the V2 prompt shape."""
    return "\n\n".join(
        [
            "CONTENT_METADATA_JSON:",
            json.dumps(_content_metadata(content), ensure_ascii=False, sort_keys=True),
            "SOURCE_TEXT:",
            content.text,
        ]
    )


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

    return "\n\n".join(
        [
            "SCORING_CONTEXT_JSON:",
            json.dumps(score_payload, ensure_ascii=False, sort_keys=True),
            "CONTENT_METADATA_JSON:",
            json.dumps(_content_metadata(content), ensure_ascii=False, sort_keys=True),
            "SOURCE_TEXT:",
            content.text,
        ]
    )


def _build_telegram_input(
    score: ScoreResult,
    extraction: ExtractionResult,
    content: FetchedContent | None,
) -> str:
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
    extraction_payload = getattr(extraction, "parsed", None)
    if not isinstance(extraction_payload, dict):
        extraction_payload = {
            "title": getattr(extraction, "title", ""),
            "one_line_signal": getattr(extraction, "one_line_signal", ""),
        }
    payload = {
        "score": score_payload,
        "extraction": extraction_payload,
        "content": _content_metadata(content) if content is not None else {},
    }
    return "TELEGRAM_INPUT_JSON:\n" + json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _compact_items(value: object, *, limit: int = 2) -> list[str]:
    items = value if isinstance(value, list) else [value] if isinstance(value, str) and value else []
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = (
                item.get("claim")
                or item.get("inference")
                or item.get("action")
                or item.get("trigger")
                or item.get("summary")
                or _stringify(item)
            )
        else:
            text = _stringify(item)
        text = " ".join(str(text).split())
        if text:
            lines.append(text)
        if len(lines) >= limit:
            break
    return lines


def _format_telegram_v2_cn(
    score: ScoreResult,
    extraction: ExtractionResult,
    content: FetchedContent | None,
) -> str:
    """Local fallback that mirrors the V2 stable Chinese Telegram shape."""
    parsed = _as_dict(getattr(extraction, "parsed", {}))
    score_parsed = _as_dict(getattr(score, "parsed", {}))
    source_score = _as_dict(parsed.get("source_score") or score_parsed.get("source_score"))
    compression = _as_dict(parsed.get("content_compression") or score_parsed.get("content_compression"))

    title = str(parsed.get("refactored_title") or extraction.title or "未命名内容")
    category = str(parsed.get("category") or getattr(score, "signal_tier", "") or "未分类")
    summary = str(parsed.get("one_line_summary") or extraction.one_line_signal or "暂无摘要")
    link = (
        content.url if content is not None else
        str(parsed.get("original_url") or parsed.get("url") or score_parsed.get("url") or "")
    )

    source_type = str(parsed.get("source_type") or getattr(score, "source_type", "") or "Unknown")
    source_tier = str(parsed.get("source_tier") or getattr(score, "source_tier", "") or "Unknown")
    interest_flag = str(parsed.get("interest_flag") or getattr(score, "interest_flag", "") or "Unknown")
    source_line = f"信源: {source_type}/{source_tier}，{interest_flag}"
    if source_score.get("L1_score") not in (None, ""):
        source_line += f"，L1={_format_score(source_score.get('L1_score'))}"

    compressed_signal = str(compression.get("compressed_signal") or extraction.one_line_signal or "")
    dropped_noise = _compact_items(compression.get("dropped_noise"), limit=1)
    compression_line = compressed_signal or "暂无压缩信号"
    if dropped_noise:
        compression_line += f"；已丢弃: {dropped_noise[0]}"

    experience = _compact_items(parsed.get("why_it_matters"), limit=2)
    signals = _compact_items(parsed.get("inferences"), limit=1)
    signals.insert(0, str(extraction.one_line_signal or summary))
    signals = [item for item in signals if item][:2]
    quotes = _compact_items(parsed.get("evidence"), limit=2)
    actions = _compact_items(parsed.get("recommended_actions") or parsed.get("monitoring_triggers"), limit=1)

    lines = [
        f"🎯 {title}",
        f"🏷 {category}",
        "",
        f"💡 {summary[:120]}",
    ]

    if experience:
        lines.extend(["", "🗣 1. 经验萃取"])
        lines.extend(f"▪️ {item}" for item in experience)

    if signals:
        lines.extend(["", "📡 2. 信号萃取"])
        lines.extend(f"▪️ {item}" for item in signals)

    lines.extend(
        [
            "",
            "🧭 3. 信源与压缩",
            f"▪️ {source_line}",
            f"▪️ 压缩: {compression_line}",
        ]
    )

    if quotes:
        lines.extend(["", "💬 4. 核心金句"])
        lines.extend(f"\"{item[:160]}\"" for item in quotes)

    if actions:
        lines.extend(["", "🛠 5. 下一步"])
        lines.extend(f"▪️ {item}" for item in actions)

    if link:
        lines.extend(["", f"🔗 阅读原文: {link}"])

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# LiveLLMProvider
# ---------------------------------------------------------------------------


class LiveLLMProvider:
    """Real LLM provider with configurable routing and retry semantics.

    Provider selection and API keys come from config and environment.
    HTTP errors are mapped to queue FailureKind for proper retry logic.

    Supports fallback providers: when the primary provider returns 429 or
    quota exhaustion, automatically tries the next provider in the chain.
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
        self._circuit_open_until: dict[str, float] = {}  # Per-provider circuit breaker
        self._all_providers = config.all_provider_configs()

    @property
    def model_route(self) -> str:
        """Generate stable route key including all provider configurations."""
        parts = []
        for idx, p in enumerate(self._all_providers):
            provider = p.get("provider", "unknown")
            api_key_env = p.get("api_key_env", "")
            model = p.get("scoring_model") or p.get("extraction_model") or p.get("telegram_model") or ""
            api_base = p.get("api_base", "")
            parts.append(f"{idx}:{provider}:{api_key_env}:{model}:{api_base}")
        return f"live://{'|'.join(parts)}"

    def _is_provider_available(self, provider_config: dict) -> bool:
        """Check if provider circuit breaker is open using stable route key."""
        route_key = self._provider_route_key(provider_config)
        open_until = self._circuit_open_until.get(route_key, 0.0)
        return time.time() >= open_until

    def _mark_provider_unavailable(self, provider_config: dict, minutes: int = 5) -> None:
        """Open circuit breaker for a provider using stable route key."""
        route_key = self._provider_route_key(provider_config)
        self._circuit_open_until[route_key] = time.time() + (minutes * 60)

    @staticmethod
    def _provider_route_key(provider_config: dict) -> str:
        """Generate stable route key for a provider config."""
        provider = provider_config.get("provider", "unknown")
        api_key_env = provider_config.get("api_key_env", "")
        model = (
            provider_config.get("scoring_model")
            or provider_config.get("extraction_model")
            or provider_config.get("telegram_model")
            or ""
        )
        api_base = provider_config.get("api_base", "")
        return f"{provider}:{api_key_env}:{model}:{api_base}"

    def score(self, content: FetchedContent, prompt: str) -> str | TypedError:
        """Run scoring prompt against configured LLM with fallback."""
        for provider_config in self._all_providers:
            if not self._is_provider_available(provider_config):
                continue
            model = provider_config.get("scoring_model") or _DEFAULT_MODELS.get(
                provider_config.get("provider", ""), "gpt-4o-mini"
            )
            result = self._call_llm(
                _build_scoring_input(content),
                prompt,
                model,
                stage="score",
                provider_config=provider_config,
            )
            if not isinstance(result, TypedError):
                return result
            # If error is not rate limit/quota, don't try fallback
            if result.failure_kind not in (FailureKind.LLM_RATE_LIMIT, FailureKind.LLM_QUOTA_EXHAUSTED):
                return result

        # All providers failed
        return TypedError(
            failure_kind=FailureKind.LLM_QUOTA_EXHAUSTED,
            message="All LLM providers are rate-limited or quota exhausted",
            stage="score",
            retryable=True,
            next_action=NextAction.RETRY_LATER,
            next_retry_at=retry_at(15),
        )

    def extract(
        self,
        content: FetchedContent,
        score: ScoreResult,
        prompt: str,
    ) -> str | TypedError:
        """Run extraction prompt against configured LLM with fallback."""
        for provider_config in self._all_providers:
            if not self._is_provider_available(provider_config):
                continue
            model = provider_config.get("extraction_model") or _DEFAULT_MODELS.get(
                provider_config.get("provider", ""), "gpt-4o-mini"
            )
            result = self._call_llm(
                _build_extraction_input(content, score),
                prompt,
                model,
                stage="extract",
                provider_config=provider_config,
            )
            if not isinstance(result, TypedError):
                return result
            if result.failure_kind not in (FailureKind.LLM_RATE_LIMIT, FailureKind.LLM_QUOTA_EXHAUSTED):
                return result

        return TypedError(
            failure_kind=FailureKind.LLM_QUOTA_EXHAUSTED,
            message="All LLM providers are rate-limited or quota exhausted",
            stage="extract",
            retryable=True,
            next_action=NextAction.RETRY_LATER,
            next_retry_at=retry_at(15),
        )

    def format_telegram(
        self,
        score: ScoreResult,
        extraction: ExtractionResult,
        prompt: str,
        *,
        content: FetchedContent | None = None,
    ) -> str | TypedError:
        """Format Telegram with the V2 stable prompt when configured, else local Chinese fallback."""
        telegram_input = _build_telegram_input(score, extraction, content)

        for provider_config in self._all_providers:
            if not self._is_provider_available(provider_config):
                continue
            model = str(provider_config.get("telegram_model") or "")
            if not model:
                continue
            result = self._call_llm(
                telegram_input,
                prompt,
                model,
                stage="telegram_format",
                provider_config=provider_config,
            )
            if not isinstance(result, TypedError) and result.strip():
                return result.strip()
            if isinstance(result, TypedError) and result.failure_kind in (
                FailureKind.LLM_RATE_LIMIT,
                FailureKind.LLM_QUOTA_EXHAUSTED,
            ):
                continue
            break

        return _format_telegram_v2_cn(score, extraction, content)

    def _call_llm(
        self,
        content: str,
        prompt: str,
        model: str,
        *,
        stage: str = "llm_call",
        provider_config: dict | None = None,
    ) -> str | TypedError:
        """Make LLM API call with error mapping and fallback logic.

        For 429/quota errors: immediately switch to next provider (no retry).
        For timeout/5xx errors: retry up to max_retries on same provider.
        """
        if provider_config is None:
            provider_config = {
                "provider": self._config.provider,
                "api_key_env": self._config.api_key_env,
                "api_key": self._config.api_key,
                "api_base": self._config.api_base,
                "temperature": self._config.temperature,
            }

        provider_name = provider_config.get("provider", "unknown")
        api_key_env = provider_config.get("api_key_env", "")
        api_base = str(provider_config.get("api_base", ""))
        temperature = float(provider_config.get("temperature", self._config.temperature))

        # 优先使用直接配置的 api_key，然后从环境变量读取
        api_key = provider_config.get("api_key", "") or self._env.get(api_key_env, "")
        if not api_key:
            return TypedError(
                failure_kind=FailureKind.AUTH_INVALID,
                message=f"API key not found: checked config.api_key and environment {api_key_env}",
                stage=stage,
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=f"Provider: {provider_name}. Configure the API key in config.local.yaml (llm.api_key) or environment variable ({api_key_env})",
            )

        if not self._is_provider_available(provider_config):
            return TypedError(
                failure_kind=FailureKind.LLM_RATE_LIMIT,
                message=f"LLM circuit breaker is open for {self._provider_route_key(provider_config)}",
                stage=stage,
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                next_retry_at=retry_at(5),
            )

        builder = _REQUEST_BUILDERS.get(provider_name, _build_openai_request)
        url, headers, data = builder(content, prompt, model, api_key, api_base, temperature)

        last_error: TypedError | None = None
        response: _HTTPResponse | None = None

        for attempt in range(max(1, self._config.max_retries + 1)):
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

            # For 429/quota errors, open circuit and return immediately (no retry)
            if failure_kind in (FailureKind.LLM_RATE_LIMIT, FailureKind.LLM_QUOTA_EXHAUSTED):
                self._mark_provider_unavailable(provider_config, _retry_after_minutes(response.body))
                return last_error

            # For non-retryable errors, return immediately
            if not retryable:
                return last_error

            # For timeout/5xx, continue retry loop

        if last_error is not None:
            return last_error

        assert response is not None

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
    """Create LiveLLMProvider from V3Config.llm section.

    The llm_config must have fallback_providers already loaded from YAML.
    """
    # Extract fallback_providers from LLMConfig (already loaded from YAML)
    fallback_providers = getattr(llm_config, "fallback_providers", []) or []

    config = LiveLLMConfig(
        provider=llm_config.provider,
        api_key_env=llm_config.api_key_env,
        api_key=getattr(llm_config, "api_key", ""),
        api_base=getattr(llm_config, "api_base", ""),
        scoring_model=llm_config.scoring_model,
        extraction_model=llm_config.extraction_model,
        telegram_model=getattr(llm_config, "telegram_brief_model", ""),
        request_timeout_seconds=llm_config.request_timeout_seconds,
        max_retries=llm_config.max_retries,
        min_delay_seconds=llm_config.min_delay_seconds,
        temperature=getattr(llm_config, "temperature", 0.1),
        fallback_providers=fallback_providers,
    )
    return LiveLLMProvider(config, env=env, http_post=http_post)
