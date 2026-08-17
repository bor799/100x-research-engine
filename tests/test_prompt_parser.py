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


def test_parse_score_result_recomputes_aggregates_from_numeric_dimensions():
    """V3 bundles emit numeric L1-L4; aggregates are code-owned (linear blend).

    Regression for the L1*L2*L3 multiplicative crush: the same dimensions that
    produced final_score=0.51 (reject) under the old product now land above the
    0.70 archive band.
    """
    raw = json.dumps(
        _score_payload(
            score=5.1,
            final_score=0.51,
            L1=0.6,
            L2=0.7,
            L3=0.8,
            L4=0.9,
            objective_quality=0.34,
        )
    )

    result = parse_score_result(
        raw,
        prompt_bundle="v3_business_stories_v2",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    # objective_quality = 0.4*0.6 + 0.3*0.7 + 0.3*0.8 = 0.69
    # final_score = 0.7*0.69 + 0.3*0.9 = 0.753
    assert result.final_score == 0.753
    assert result.score == 7.53
    assert result.parsed["final_score"] == 0.753
    assert result.parsed["score"] == 7.53
    assert result.parsed["objective_quality"] == 0.69


def test_parse_score_result_low_l1_survives_linear_blend():
    """A harsh L1 no longer zeroes the item: the model's multiplicative output
    (final_score=0.04) is overridden by the code-side blend."""
    raw = json.dumps(
        _score_payload(
            score=0.4,
            final_score=0.04,
            L1=0.2,
            L2=0.5,
            L3=0.6,
            L4=0.9,
            objective_quality=0.06,
        )
    )

    result = parse_score_result(
        raw,
        prompt_bundle="v3_business_stories_v2",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    # objective_quality = 0.4*0.2 + 0.3*0.5 + 0.3*0.6 = 0.41
    # final_score = 0.7*0.41 + 0.3*0.9 = 0.557
    assert result.final_score == 0.557


def test_parse_score_result_keeps_verbatim_aggregates_for_legacy_strings():
    """Legacy bundles emit text dimensions; their aggregates stay verbatim."""
    result = parse_score_result(
        json.dumps(_score_payload()),
        prompt_bundle="v2_legacy",
        prompt_hash="abc123",
        model_route="stub://test",
    )

    assert result.final_score == 0.85
    assert result.score == 8.5
    assert result.parsed["objective_quality"] == "high"


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
