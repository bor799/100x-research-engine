from pathlib import Path

from knowledge_extractor_v3.fetchers.fixture import FixtureFetcher
from knowledge_extractor_v3.models import FetchedContent, TypedError
from knowledge_extractor_v3.queue_store import FailureKind, NextAction


def test_fixture_fetcher_returns_standard_fetched_content():
    result = FixtureFetcher().fetch("fixture://high_signal")

    assert isinstance(result, FetchedContent)
    assert result.title == "Frontier Payments API Finds Bottom-Up Distribution"
    assert result.source == "fixture"
    assert result.source_type == "fixture"
    assert result.content_hash
    assert result.metadata["fixture_scenario"] == "high_signal"
    assert result.text


def test_fixture_fetcher_fetch_failed_returns_typed_error():
    result = FixtureFetcher().fetch("fixture://fetch_failed")

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.FETCH_FAILED
    assert result.next_action is NextAction.MANUAL_REVIEW


def test_fixture_fetcher_unknown_fixture_is_fetch_failed():
    result = FixtureFetcher().fetch("fixture://does_not_exist")

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.FETCH_FAILED


def test_fixture_fetcher_can_use_caller_fixture_map(tmp_path):
    fixture = tmp_path / "custom.md"
    fixture.write_text("# Custom Source\n\nFixture body.", encoding="utf-8")
    fetcher = FixtureFetcher(fixture_map={"custom": fixture})

    result = fetcher.fetch("fixture://custom")

    assert isinstance(result, FetchedContent)
    assert result.title == "Custom Source"
    assert Path(result.metadata["fixture_path"]) == fixture
