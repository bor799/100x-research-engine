"""Tests for live LLM provider with fake HTTP transport."""

import json
import os
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from knowledge_extractor_v3.llm.live_provider import (
    LiveLLMProvider,
    LiveLLMConfig,
    _map_http_error,
    _HTTPResponse,
)
from knowledge_extractor_v3.models import FetchedContent
from knowledge_extractor_v3.queue_store import FailureKind, NextAction


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class FakeHTTPPost:
    """Fake HTTP post for testing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.response_status = 200
        self.response_body = ""
        self.fail_after = -1

    def __call__(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: bytes,
        timeout: int,
    ) -> _HTTPResponse:
        self.calls.append({
            "url": url,
            "headers": headers,
            "data": data,
            "timeout": timeout,
        })
        if self.fail_after >= 0 and len(self.calls) > self.fail_after:
            response = _HTTPResponse()
            response.status_code = 500
            response.body = "Simulated server error"
            return response

        response = _HTTPResponse()
        response.status_code = self.response_status
        response.body = self.response_body
        return response


def _success_response(content: str) -> str:
    """Create a successful OpenAI-format response."""
    return json.dumps({
        "choices": [{
            "message": {"content": content},
        }],
    })


def _anthropic_success_response(content: str) -> str:
    """Create a successful Anthropic-format response."""
    return json.dumps({
        "content": [{"type": "text", "text": content}],
    })


def make_test_content(text: str = "Test article content") -> FetchedContent:
    return FetchedContent(
        url="https://example.com/test",
        source="test",
        source_type="web",
        title="Test Article",
        text=text,
        fetched_at="2024-01-01T00:00:00+00:00",
        content_hash="abc123",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_live_provider_score_success():
    """Successful scoring request."""
    http = FakeHTTPPost()
    http.response_body = _success_response('{"score": 8, "final_score": 0.8, "signal_tier": "A"}')

    config = LiveLLMConfig(provider="openai")
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=http)

    content = make_test_content()
    result = provider.score(content, "Score this article")

    assert isinstance(result, str)
    assert result.startswith('{"score"')
    assert len(http.calls) == 1
    assert http.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"
    request_payload = json.loads(http.calls[0]["data"].decode("utf-8"))
    request_text = request_payload["messages"][0]["content"]
    assert "CONTENT_METADATA_JSON" in request_text
    assert "SOURCE_TEXT" in request_text


def test_live_provider_zhipu_appends_chat_completions_to_configured_api_base():
    """Zhipu request appends chat/completions to the configured API base."""
    http = FakeHTTPPost()
    http.response_body = _success_response('{"score": 8, "final_score": 0.8, "signal_tier": "A"}')

    config = LiveLLMConfig(
        provider="zhipu",
        api_key="direct-key",
        api_base="https://open.bigmodel.cn/api/coding/paas/v4",
        scoring_model="GLM-4.5",
        temperature=0.1,
    )
    provider = LiveLLMProvider(config, env={}, http_post=http)

    result = provider.score(make_test_content(), "Score this article")

    assert isinstance(result, str)
    assert http.calls[0]["url"] == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    request_payload = json.loads(http.calls[0]["data"].decode("utf-8"))
    assert request_payload["model"] == "GLM-4.5"
    assert request_payload["temperature"] == 0.1


def test_live_provider_extract_success():
    """Successful extraction request."""
    http = FakeHTTPPost()
    http.response_body = _success_response('{"title": "Test", "one_line_signal": "Test signal"}')

    config = LiveLLMConfig(provider="openai")
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=http)

    content = make_test_content()
    # Mock score_result as a simple dict-like object
    from types import SimpleNamespace
    score_ns = SimpleNamespace(
        score=8,
        final_score=0.8,
        signal_tier="A",
    )

    result = provider.extract(content, score_ns, "Extract this")  # type: ignore

    assert isinstance(result, str)
    assert result.startswith('{"title"')
    request_payload = json.loads(http.calls[0]["data"].decode("utf-8"))
    request_text = request_payload["messages"][0]["content"]
    assert "SCORING_CONTEXT_JSON" in request_text
    assert '"final_score": 0.8' in request_text
    assert "SOURCE_TEXT" in request_text


def test_live_provider_format_telegram():
    """Telegram formatting uses local Chinese fallback when no telegram model is configured."""
    http = FakeHTTPPost()

    config = LiveLLMConfig(provider="openai")
    provider = LiveLLMProvider(config, http_post=http)

    from types import SimpleNamespace
    score = SimpleNamespace(
        score=8.5,
        final_score=0.85,
        signal_tier="A",
        decision_window_status="open",
        source_type="SocialPost",
        source_tier="Primary",
        interest_flag="Independent",
        attribution_chain="tweet -> evidence -> signal",
        parsed={"url": "https://example.com/article"},
    )
    extraction = SimpleNamespace(
        title="Test Article",
        one_line_signal="A test signal about something",
        parsed={
            "decision_window_status": "open",
            "source_type": "SocialPost",
            "source_tier": "Primary",
            "interest_flag": "Independent",
            "why_it_matters": ["Useful for sourcing"],
            "evidence": [{"id": "E1", "claim": "A concrete claim", "provenance": "tweet"}],
            "recommended_actions": ["Follow up"],
            "attribution_chain": "tweet -> E1 -> signal",
        },
    )

    result = provider.format_telegram(score, extraction, "ignored prompt")

    assert isinstance(result, str)
    assert "🎯 Test Article" in result
    assert "📡 2. 信号萃取" in result
    assert "A test signal" in result
    assert "A concrete claim" in result
    # No HTTP call should be made
    assert len(http.calls) == 0


def test_live_provider_format_telegram_uses_prompt_model_when_configured():
    """Telegram formatting can use the configured V2 stable prompt model."""
    http = FakeHTTPPost()
    http.response_body = _success_response("TG READY")

    config = LiveLLMConfig(provider="openai", telegram_model="gpt-4o-mini")
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=http)

    from types import SimpleNamespace
    score = SimpleNamespace(
        score=8.5,
        final_score=0.85,
        signal_tier="A",
        decision_window_status="open",
        source_type="SocialPost",
        source_tier="Primary",
        interest_flag="Independent",
        attribution_chain="tweet -> evidence -> signal",
        parsed={"url": "https://example.com/article"},
    )
    extraction = SimpleNamespace(
        title="Test Article",
        one_line_signal="A test signal about something",
        parsed={"recommended_actions": ["Follow up"]},
    )

    result = provider.format_telegram(score, extraction, "Format for Telegram", content=make_test_content())

    assert result == "TG READY"
    assert len(http.calls) == 1
    request_payload = json.loads(http.calls[0]["data"].decode("utf-8"))
    request_text = request_payload["messages"][0]["content"]
    assert "Format for Telegram" in request_text
    assert "TELEGRAM_INPUT_JSON" in request_text


def test_live_provider_missing_api_key():
    """Missing API key returns proper error."""
    http = FakeHTTPPost()
    config = LiveLLMConfig(
        provider="openai",
        api_key_env="NONEXISTENT_KEY",
    )
    provider = LiveLLMProvider(config, env={}, http_post=http)

    content = make_test_content()
    result = provider.score(content, "Score this")

    from knowledge_extractor_v3.models import TypedError
    assert isinstance(result, TypedError)
    assert result.failure_kind == FailureKind.AUTH_INVALID
    assert "API key not found" in result.message
    # The error message should mention the missing env var name
    assert "NONEXISTENT_KEY" in result.message


def test_map_http_error_rate_limit():
    """HTTP 429 maps to rate limit."""
    failure_kind, next_action, retryable = _map_http_error(429, "too many requests", "score")

    assert failure_kind == FailureKind.LLM_RATE_LIMIT
    assert next_action == NextAction.RETRY_LATER
    assert retryable is True


def test_map_http_error_timeout():
    """HTTP 408 maps to timeout."""
    failure_kind, next_action, retryable = _map_http_error(408, "request timeout", "extract")

    assert failure_kind == FailureKind.LLM_TIMEOUT
    assert next_action == NextAction.RETRY_LATER
    assert retryable is True


def test_map_http_error_auth():
    """HTTP 401/403 maps to auth error."""
    failure_kind, next_action, retryable = _map_http_error(401, "unauthorized", "score")

    assert failure_kind == FailureKind.AUTH_INVALID
    assert next_action == NextAction.MANUAL_REVIEW
    assert retryable is False


def test_map_http_error_server():
    """HTTP 5xx maps to retryable timeout."""
    failure_kind, next_action, retryable = _map_http_error(503, "service unavailable", "score")

    assert failure_kind == FailureKind.LLM_TIMEOUT
    assert next_action == NextAction.RETRY_LATER
    assert retryable is True


def test_map_http_error_client():
    """HTTP 4xx (except auth) maps to parse error (terminal)."""
    failure_kind, next_action, retryable = _map_http_error(400, "bad request", "score")

    assert failure_kind == FailureKind.PARSE_ERROR
    assert next_action == NextAction.MANUAL_REVIEW
    assert retryable is False


def test_live_provider_http_error_returns_typed_error():
    """HTTP error from provider returns TypedError with correct mapping.

    NOTE: After implementing immediate fallback on 429, when all providers fail
    we return LLM_QUOTA_EXHAUSTED (not LLM_RATE_LIMIT) to trigger cooldown.
    """
    http = FakeHTTPPost()
    http.response_status = 429
    http.response_body = "Rate limit exceeded"

    config = LiveLLMConfig(provider="openai")
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=http)

    content = make_test_content()
    result = provider.score(content, "Score this")

    from knowledge_extractor_v3.models import TypedError
    assert isinstance(result, TypedError)
    # All providers failed -> quota exhausted (triggers cooldown)
    assert result.failure_kind == FailureKind.LLM_QUOTA_EXHAUSTED
    assert result.stage == "score"
    assert result.retryable is True
    assert result.next_action == NextAction.RETRY_LATER


def test_live_provider_retries_retryable_errors():
    """Provider retries transient errors before returning success."""
    http = FakeHTTPPost()
    http.response_body = _success_response('{"score": 8, "final_score": 0.8, "signal_tier": "A"}')
    http.fail_after = 0

    # Override fake behavior: first call fails, second succeeds.
    def flaky_post(url, *, headers, data, timeout):
        http.calls.append({"url": url, "headers": headers, "data": data, "timeout": timeout})
        response = _HTTPResponse()
        if len(http.calls) == 1:
            response.status_code = 503
            response.body = "temporary unavailable"
        else:
            response.status_code = 200
            response.body = _success_response('{"score": 8, "final_score": 0.8, "signal_tier": "A"}')
        return response

    config = LiveLLMConfig(provider="openai", max_retries=1, min_delay_seconds=0)
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=flaky_post)

    result = provider.score(make_test_content(), "Score this")

    assert isinstance(result, str)
    assert len(http.calls) == 2


def test_live_provider_anthropic_response_parsing():
    """Anthropic response format is correctly parsed."""
    http = FakeHTTPPost()
    http.response_body = _anthropic_success_response('{"key": "value"}')

    config = LiveLLMConfig(provider="anthropic")
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=http)

    content = make_test_content()
    result = provider.score(content, "Score this")

    assert isinstance(result, str)
    assert result == '{"key": "value"}'


def test_live_provider_empty_response():
    """Empty response body returns parse error."""
    http = FakeHTTPPost()
    http.response_body = "{}"

    config = LiveLLMConfig(provider="openai")
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=http)

    content = make_test_content()
    result = provider.score(content, "Score this")

    from knowledge_extractor_v3.models import TypedError
    assert isinstance(result, TypedError)
    assert result.failure_kind == FailureKind.PARSE_ERROR
    assert "empty response" in result.message


def test_live_provider_model_route_property():
    """model_route reflects configured provider with stable route key.

    The new format includes provider index, name, api_key_env, and model
    for proper circuit breaker isolation per provider configuration.
    """
    config = LiveLLMConfig(provider="zhipu")
    provider = LiveLLMProvider(config)

    # Format: live://<index>:<provider>:<api_key_env>:<model>
    assert provider.model_route.startswith("live://0:zhipu:ZHIPU_API_KEY:")

    config = LiveLLMConfig(provider="anthropic")
    provider = LiveLLMProvider(config)

    assert provider.model_route.startswith("live://0:anthropic:ZHIPU_API_KEY:")

    # Test with fallback providers
    config = LiveLLMConfig(
        provider="zhipu",
        fallback_providers=[
            {"provider": "openai", "api_key_env": "OPENAI_API_KEY"},
        ],
    )
    provider = LiveLLMProvider(config)

    # Should contain both providers in route
    assert "0:zhipu:ZHIPU_API_KEY:" in provider.model_route
    assert "1:openai:OPENAI_API_KEY:" in provider.model_route


def test_live_provider_non_json_response():
    """Non-JSON response is returned as-is."""
    http = FakeHTTPPost()
    http.response_body = "Plain text response from API"

    config = LiveLLMConfig(provider="openai")
    provider = LiveLLMProvider(config, env={"ZHIPU_API_KEY": "test-key"}, http_post=http)

    content = make_test_content()
    result = provider.score(content, "Score this")

    assert isinstance(result, str)
    assert result == "Plain text response from API"


def test_create_live_provider_helper():
    """Helper function creates provider from config."""
    from knowledge_extractor_v3.config_loader import LLMConfig
    from knowledge_extractor_v3.llm.live_provider import create_live_provider

    llm_config = LLMConfig(
        provider="zhipu",
        api_key_env="TEST_KEY",
        api_base="https://open.bigmodel.cn/api/coding/paas/v4",
        scoring_model="test-model",
    )

    provider = create_live_provider(llm_config, env={"TEST_KEY": "secret"})

    # Check model_route format includes index, provider, key env, and model
    assert provider.model_route.startswith("live://0:zhipu:TEST_KEY:test-model")
    assert provider._config.scoring_model == "test-model"
    assert provider._config.api_base == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert provider._config.telegram_model == ""


if __name__ == "__main__":
    import traceback

    tests = [
        test_live_provider_score_success,
        test_live_provider_zhipu_appends_chat_completions_to_configured_api_base,
        test_live_provider_extract_success,
        test_live_provider_format_telegram,
        test_live_provider_format_telegram_uses_prompt_model_when_configured,
        test_live_provider_missing_api_key,
        test_map_http_error_rate_limit,
        test_map_http_error_timeout,
        test_map_http_error_auth,
        test_map_http_error_server,
        test_map_http_error_client,
        test_live_provider_http_error_returns_typed_error,
        test_live_provider_anthropic_response_parsing,
        test_live_provider_empty_response,
        test_live_provider_model_route_property,
        test_live_provider_non_json_response,
        test_create_live_provider_helper,
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
