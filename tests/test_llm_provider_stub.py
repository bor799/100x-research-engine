"""Stub provider error semantics on the V4 absorption path."""

from knowledge_extractor_v3.fetchers.fixture import FixtureFetcher
from knowledge_extractor_v3.llm.provider import StubLLMProvider
from knowledge_extractor_v3.models import TypedError
from knowledge_extractor_v3.prompt_parser import parse_absorption_result
from knowledge_extractor_v3.queue_store import FailureKind, NextAction

ABSORPTION_PROMPT = "V4 absorption prompt requiring information_gain/action_value/relevance"


def _content(url: str):
    content = FixtureFetcher().fetch(url)
    assert not isinstance(content, TypedError)
    return content


def _parse(raw: str):
    return parse_absorption_result(
        raw,
        prompt_bundle="v4_absorption",
        prompt_hash="hash",
        model_route=StubLLMProvider.model_route,
    )


def test_stub_absorption_parse_error_is_terminal():
    provider = StubLLMProvider()
    raw = provider.score(_content("fixture://parse_error"), ABSORPTION_PROMPT)
    assert not isinstance(raw, TypedError)
    parsed = _parse(raw)
    assert isinstance(parsed, TypedError)
    assert parsed.failure_kind is FailureKind.PARSE_ERROR


def test_stub_llm_provider_rate_limit_is_retryable():
    provider = StubLLMProvider()
    error = provider.score(_content("fixture://llm_rate_limit"), ABSORPTION_PROMPT)
    assert isinstance(error, TypedError)
    assert error.failure_kind is FailureKind.LLM_RATE_LIMIT
    assert error.retryable
    assert error.next_action is NextAction.RETRY_LATER
    assert error.next_retry_at


def test_stub_llm_provider_timeout_is_retryable():
    provider = StubLLMProvider()
    error = provider.score(_content("fixture://llm_timeout"), ABSORPTION_PROMPT)
    assert isinstance(error, TypedError)
    assert error.failure_kind is FailureKind.LLM_TIMEOUT
    assert error.retryable
    assert error.next_retry_at
