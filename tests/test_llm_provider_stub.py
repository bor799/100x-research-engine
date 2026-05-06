from knowledge_extractor_v3.fetchers.fixture import FixtureFetcher
from knowledge_extractor_v3.llm.provider import StubLLMProvider
from knowledge_extractor_v3.models import TypedError
from knowledge_extractor_v3.prompt_parser import parse_score_result
from knowledge_extractor_v3.queue_store import FailureKind, NextAction


def _content(url: str):
    content = FixtureFetcher().fetch(url)
    assert not isinstance(content, TypedError)
    return content


def test_stub_llm_provider_scores_high_signal_fixture():
    provider = StubLLMProvider()

    raw = provider.score(_content("fixture://high_signal"), "prompt")
    assert not isinstance(raw, TypedError)
    parsed = parse_score_result(
        raw,
        prompt_bundle="primary_market_v1",
        prompt_hash="hash",
        model_route=provider.model_route,
    )

    assert parsed.signal_tier == "A"
    assert parsed.final_score > 0.8


def test_stub_llm_provider_scores_low_quality_as_reject():
    provider = StubLLMProvider()

    raw = provider.score(_content("fixture://low_quality"), "prompt")
    assert not isinstance(raw, TypedError)
    parsed = parse_score_result(
        raw,
        prompt_bundle="primary_market_v1",
        prompt_hash="hash",
        model_route=provider.model_route,
    )

    assert parsed.signal_tier == "Reject"
    assert parsed.final_score < 0.3


def test_stub_llm_provider_can_emit_parse_error_payload():
    provider = StubLLMProvider()

    raw = provider.score(_content("fixture://parse_error"), "prompt")
    assert not isinstance(raw, TypedError)
    parsed = parse_score_result(
        raw,
        prompt_bundle="primary_market_v1",
        prompt_hash="hash",
        model_route=provider.model_route,
    )

    assert isinstance(parsed, TypedError)
    assert parsed.failure_kind is FailureKind.PARSE_ERROR


def test_stub_llm_provider_rate_limit_is_retryable():
    provider = StubLLMProvider()

    error = provider.score(_content("fixture://llm_rate_limit"), "prompt")

    assert isinstance(error, TypedError)
    assert error.failure_kind is FailureKind.LLM_RATE_LIMIT
    assert error.retryable
    assert error.next_action is NextAction.RETRY_LATER
    assert error.next_retry_at


def test_stub_llm_provider_timeout_is_retryable():
    provider = StubLLMProvider()

    error = provider.score(_content("fixture://llm_timeout"), "prompt")

    assert isinstance(error, TypedError)
    assert error.failure_kind is FailureKind.LLM_TIMEOUT
    assert error.retryable
    assert error.next_retry_at
