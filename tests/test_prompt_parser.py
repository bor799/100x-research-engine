import json

from knowledge_extractor_v3.models import TypedError
from knowledge_extractor_v3.prompt_parser import parse_extraction_result, parse_score_result
from knowledge_extractor_v3.queue_store import FailureKind, NextAction


def _score_payload(**overrides):
    payload = {
        "score": 8.5,
        "final_score": 0.85,
        "signal_tier": "A",
        "L1": "market timing",
        "L2": "distribution",
        "L3": "proof",
        "L4": "action",
        "objective_quality": "high",
        "decision_window_status": "open",
        "source_type": "fixture",
        "source_tier": "primary",
        "interest_flag": "track",
        "attribution_chain": ["fixture", "fixture://high_signal"],
        "rationale": "useful",
        "key_claims": ["claim"],
        "watch_items": ["watch"],
    }
    payload.update(overrides)
    return payload


def _extraction_payload(**overrides):
    payload = {
        "title": "Frontier Payments",
        "one_line_signal": "Worth tracking.",
        "decision_window_status": "open",
        "source_type": "fixture",
        "source_tier": "primary",
        "interest_flag": "track",
        "attribution_chain": ["fixture"],
        "why_it_matters": ["why"],
        "evidence": ["evidence"],
        "inferences": ["inference"],
        "risks_and_conflicts": ["risk"],
        "recommended_actions": ["action"],
        "monitoring_triggers": ["trigger"],
        "obsidian_brief_markdown": "# Frontier Payments\n\nBrief.",
    }
    payload.update(overrides)
    return payload


def test_parse_score_result_accepts_fenced_json():
    raw = "```json\n" + json.dumps(_score_payload()) + "\n```"

    result = parse_score_result(
        raw,
        prompt_bundle="primary_market_v1",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    assert result.score == 8.5
    assert result.final_score == 0.85
    assert result.signal_tier == "A"


def test_parse_score_result_rejects_invalid_json():
    result = parse_score_result(
        "{not json",
        prompt_bundle="primary_market_v1",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.PARSE_ERROR
    assert result.next_action is NextAction.INVESTIGATE


def test_parse_score_result_rejects_missing_required_field():
    payload = _score_payload()
    del payload["watch_items"]

    result = parse_score_result(
        json.dumps(payload),
        prompt_bundle="primary_market_v1",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.PARSE_ERROR
    assert "watch_items" in result.detail


def test_parse_score_result_rejects_string_numbers():
    result = parse_score_result(
        json.dumps(_score_payload(final_score="0.9")),
        prompt_bundle="primary_market_v1",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.PARSE_ERROR
    assert "final_score" in result.message


def test_parse_extraction_result_requires_obsidian_markdown():
    result = parse_extraction_result(
        json.dumps(_extraction_payload(obsidian_brief_markdown="")),
        prompt_bundle="primary_market_v1",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.PARSE_ERROR
    assert "obsidian_brief_markdown" in result.detail


def test_parse_extraction_result_returns_typed_result():
    result = parse_extraction_result(
        json.dumps(_extraction_payload()),
        prompt_bundle="primary_market_v1",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    assert result.title == "Frontier Payments"
    assert result.obsidian_brief_markdown.startswith("# Frontier Payments")
