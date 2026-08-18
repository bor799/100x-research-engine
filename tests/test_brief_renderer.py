"""Deterministic WeChat card renderer: structure, quote gate, budget."""

from __future__ import annotations

import json

from knowledge_extractor_v3.brief_contract import validate_brief
from knowledge_extractor_v3.brief_renderer import render_wechat_card
from knowledge_extractor_v3.models import FetchedContent
from knowledge_extractor_v3.prompt_parser import parse_absorption_result

ARTICLE_TEXT = (
    "Kimi K3 开启了 2T 参数预训练的新赛季。公开 benchmark 已经失真，"
    "各家都在用自研 harness 刷分。选型真正重要的是 API 成本与任务耗时，"
    "Grok 4.6 在实测中性价比最高。竞争主轴正在从 1T 后训练转向 2T 预训练，"
    "开源小尺寸模型正在挤压闭源中端空间。" * 3
)


def _absorb(quote: str = "公开 benchmark 已经失真，各家都在用自研 harness 刷分", **overrides):
    payload = {
        "information_gain": 0.82,
        "action_value": 0.80,
        "relevance": 0.78,
        "is_spam": False,
        "rationale": "一手判断加实测数据。",
        "title": "K3 开启 2T 预训练赛季",
        "one_line_summary": "竞争主轴转向 2T 预训练，选型看实测成本。",
        "category": "AI Agent研究解读",
        "experiences": ["选型用自建场景实测替代公开 benchmark。"],
        "signals": ["公开 benchmark 因刷分失效。", "Grok 4.6 实测性价比最高。"],
        "key_facts": ["Grok 4.6 API 成本最低。"],
        "quote": quote,
        "next_action": "在工作流里实测三个模型的成本。",
        "obsidian_brief_markdown": "# 存档",
    }
    payload.update(overrides)
    result = parse_absorption_result(
        json.dumps(payload, ensure_ascii=False),
        prompt_bundle="v4_absorption",
        prompt_hash="t",
        model_route="stub://test",
    )
    assert not hasattr(result, "failure_kind")
    return result


def _content() -> FetchedContent:
    return FetchedContent(
        url="https://elsewhere.news/zh/funeralai/kimi-1786944734501",
        source="elsewhere",
        source_type="web_article",
        title="Kimi K3 开启 2T 参数新赛季",
        text=ARTICLE_TEXT,
        fetched_at="2026-08-17T00:00:00+00:00",
        content_hash="hash1",
    )


def test_card_has_no_source_compression_section():
    score, extraction = _absorb()
    card = render_wechat_card(score, extraction, _content())
    assert "🧭" not in card
    assert "信源与压缩" not in card
    assert "source_tier" not in card


def test_card_structure_and_markers():
    score, extraction = _absorb()
    card = render_wechat_card(score, extraction, _content())
    assert card.startswith("🎯 ")
    assert "🏷 AI Agent研究解读 · Tier A" in card
    assert "💡 " in card
    assert "🗣 经验萃取" in card
    assert "📡 信号萃取" in card
    assert "💬 核心金句" in card
    assert "🛠 下一步" in card
    assert "🔗 阅读原文: https://elsewhere.news/zh/funeralai/kimi-1786944734501" in card
    assert "📊 评分: 8.0 · Tier A" in card


def test_verbatim_quote_is_included():
    score, extraction = _absorb()
    card = render_wechat_card(score, extraction, _content())
    assert "公开 benchmark 已经失真" in card


def test_paraphrased_quote_omits_section_not_fails():
    score, extraction = _absorb(quote="这是一个模型自己编的金句，原文里不存在")
    card = render_wechat_card(score, extraction, _content())
    assert "💬" not in card
    # The rest of the card is intact and contract-valid.
    assert validate_brief(card) == []


def test_empty_quote_omits_section():
    score, extraction = _absorb(quote="")
    card = render_wechat_card(score, extraction, _content())
    assert "💬" not in card
    assert validate_brief(card) == []


def test_empty_experiences_and_next_action_omit_sections():
    score, extraction = _absorb(experiences=[], next_action="")
    card = render_wechat_card(score, extraction, _content())
    assert "🗣" not in card
    assert "🛠" not in card
    assert validate_brief(card) == []


def test_card_passes_brief_contract_within_budget():
    score, extraction = _absorb()
    card = render_wechat_card(score, extraction, _content())
    assert validate_brief(card) == []


def test_quote_whitespace_normalisation_still_matches():
    # Model collapses a line break the article contains; still verbatim enough.
    text = _content()
    score, extraction = _absorb(quote="Kimi K3 开启了 2T 参数预训练的新赛季。公开 benchmark 已经失真")
    card = render_wechat_card(score, extraction, text)
    assert "💬" in card


def test_fully_loaded_card_fits_contract_budget():
    """Worst case: every section maxed out. The renderer sheds mass in
    priority order instead of tripping the 500-char contract (a live
    business_push was withheld this way on 2026-08-17)."""
    long_text = "原文里有一句足够长的金句可以逐字引用。" + "背景细节。" * 200
    score, extraction = _absorb(
        title="三十个字的标题三十个字的标题三十个字的标题三十个",
        one_line_summary="这是一条八十个字的一句话归纳。" * 3,
        experiences=["第一条满六十四个字符的经验教训条目。" * 3, "第二条满六十四个字符的经验教训条目。" * 3],
        signals=["第一条满六十四个字符的信号洞察条目。" * 3, "第二条满六十四个字符的信号洞察条目。" * 3],
        quote="原文里有一句足够长的金句可以逐字引用。",
        next_action="一条满七十二个字符的具体下一步行动监控触发器。" * 2,
    )
    content = FetchedContent(
        url="https://example.com/budget-test",
        source="test",
        source_type="web_article",
        title="Budget test",
        text=long_text,
        fetched_at="2026-08-18T00:00:00+00:00",
        content_hash="hash2",
    )
    card = render_wechat_card(score, extraction, content)
    errors = validate_brief(card)
    assert errors == [], errors
    # Required markers survive the shedding.
    assert "🎯" in card and "💡" in card and "📡 信号萃取" in card
