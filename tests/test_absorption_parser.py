"""V4 absorption parser: single-call scoring + extraction."""

from __future__ import annotations

import json

from knowledge_extractor_v3.llm.provider import StubLLMProvider
from knowledge_extractor_v3.models import FetchedContent, TypedError
from knowledge_extractor_v3.prompt_parser import (
    ABSORPTION_WEIGHTS,
    parse_absorption_result,
)


def _payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "information_gain": 0.82,
        "action_value": 0.80,
        "relevance": 0.78,
        "is_spam": False,
        "rationale": "一手判断加成本实测，可复用。",
        "title": "K3 开启 2T 预训练赛季",
        "one_line_summary": "竞争主轴转向 2T 预训练，选型看实测成本。",
        "category": "AI Agent研究解读",
        "experiences": ["选型用自建场景实测替代公开 benchmark。"],
        "signals": ["公开 benchmark 因刷分失效。"],
        "key_facts": ["Grok 4.6 实测性价比最高。"],
        "quote": "",
        "next_action": "在工作流里实测三个模型的成本。",
        "obsidian_brief_markdown": "# 存档",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def _parse(raw: str):
    result = parse_absorption_result(
        raw,
        prompt_bundle="v4_absorption",
        prompt_hash="abc123",
        model_route="stub://test",
    )
    assert not isinstance(result, TypedError), getattr(result, "detail", result)
    return result


def test_exact_weighted_math():
    score, _ = _parse(_payload(information_gain=0.82, action_value=0.80, relevance=0.78))
    expected = (
        ABSORPTION_WEIGHTS["information_gain"] * 0.82
        + ABSORPTION_WEIGHTS["action_value"] * 0.80
        + ABSORPTION_WEIGHTS["relevance"] * 0.78
    )
    assert score.final_score == round(expected, 4)
    assert score.score == round(expected * 10, 2)
    assert score.signal_tier == "A"  # 0.803 >= 0.75
    assert score.information_gain == 0.82
    assert score.action_value == 0.80
    assert score.relevance == 0.78
    assert score.rationale.startswith("一手判断")


def test_tier_boundaries():
    # A >= 7.5, B >= 6.0, else Reject — probed via uniform dims
    # (aligned with routing.REJECT_FINAL_SCORE_MAX since 2026-08-30).
    for dims, tier in (
        ((0.75, 0.75, 0.75), "A"),
        ((0.74, 0.74, 0.74), "B"),
        ((0.60, 0.60, 0.60), "B"),
        ((0.59, 0.59, 0.59), "Reject"),
    ):
        gain, action, relevance = dims
        score, _ = _parse(
            _payload(information_gain=gain, action_value=action, relevance=relevance)
        )
        assert score.signal_tier == tier, (dims, score.signal_tier, score.final_score)


def test_spam_forces_reject_despite_high_score():
    score, _ = _parse(_payload(information_gain=0.90, action_value=0.88, relevance=0.92, is_spam=True))
    assert score.final_score >= 0.75
    assert score.signal_tier == "Reject"
    assert score.is_spam is True
    assert score.is_promotional is True


def test_dimensions_clip_to_unit_range():
    score, _ = _parse(_payload(information_gain=1.7, action_value=-0.3, relevance=0.5))
    assert score.information_gain == 1.0
    assert score.action_value == 0.0


def test_missing_field_is_parse_error():
    raw = _payload()
    payload = json.loads(raw)
    del payload["action_value"]
    result = parse_absorption_result(
        json.dumps(payload), prompt_bundle="v4", prompt_hash="x", model_route="stub"
    )
    assert isinstance(result, TypedError)
    assert "action_value" in result.detail


def test_string_dimension_is_parse_error():
    result = parse_absorption_result(
        _payload(information_gain="0.8"), prompt_bundle="v4", prompt_hash="x", model_route="stub"
    )
    assert isinstance(result, TypedError)
    assert "information_gain" in result.message


def test_non_bool_spam_flag_is_parse_error():
    result = parse_absorption_result(
        _payload(is_spam="yes"), prompt_bundle="v4", prompt_hash="x", model_route="stub"
    )
    assert isinstance(result, TypedError)


def test_quote_empty_string_allowed_but_missing_is_error():
    score, _ = _parse(_payload(quote=""))
    assert score.parsed["quote"] == ""
    payload = json.loads(_payload())
    del payload["quote"]
    result = parse_absorption_result(
        json.dumps(payload), prompt_bundle="v4", prompt_hash="x", model_route="stub"
    )
    assert isinstance(result, TypedError)


def test_list_fields_must_be_lists():
    result = parse_absorption_result(
        _payload(signals="不是数组"), prompt_bundle="v4", prompt_hash="x", model_route="stub"
    )
    assert isinstance(result, TypedError)
    assert "signals" in result.message


def test_returns_both_score_and_extraction():
    score, extraction = _parse(_payload())
    assert extraction.title == "K3 开启 2T 预训练赛季"
    assert extraction.one_line_signal.startswith("竞争主轴")
    assert extraction.obsidian_brief_markdown == "# 存档"


def test_fenced_json_accepted():
    result = parse_absorption_result(
        "```json\n" + _payload() + "\n```", prompt_bundle="v4", prompt_hash="x", model_route="stub"
    )
    assert not isinstance(result, TypedError)


def _fixture_content(scenario: str) -> FetchedContent:
    return FetchedContent(
        url=f"fixture://{scenario}",
        source="fixture",
        source_type="IndustryMedia",
        title="Fixture article",
        text="公开 benchmark 已经失真，各家都在用自研 harness 刷分，实测成本才是选型核心。",
        fetched_at="2026-08-17T00:00:00+00:00",
        content_hash="hash123",
    )


def test_stub_absorption_payload_parses_high_signal():
    provider = StubLLMProvider()
    raw = provider.score(_fixture_content("high_signal"), "V4 prompt with information_gain")
    score, extraction = _parse(raw)  # type: ignore[arg-type]
    assert score.final_score >= 0.75
    assert score.signal_tier == "A"
    assert score.is_spam is False
    assert extraction.title == "Fixture article"
    # Stub quote is a verbatim slice of the fixture text.
    assert score.parsed["quote"] in _fixture_content("high_signal").text


def test_stub_absorption_payload_low_quality_rejects():
    provider = StubLLMProvider()
    raw = provider.score(_fixture_content("low_quality"), "V4 prompt with information_gain")
    score, _ = _parse(raw)  # type: ignore[arg-type]
    assert score.final_score < 0.40
    assert score.signal_tier == "Reject"


def test_stub_absorption_payload_spam_hard_rejects():
    provider = StubLLMProvider()
    raw = provider.score(_fixture_content("spam"), "V4 prompt with information_gain")
    score, _ = _parse(raw)  # type: ignore[arg-type]
    assert score.final_score >= 0.75  # dimensions stay high…
    assert score.signal_tier == "Reject"  # …but spam forces Reject
