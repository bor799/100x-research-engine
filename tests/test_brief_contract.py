"""Tests for the channel-neutral structured brief hard contract."""

from __future__ import annotations

from knowledge_extractor_v3.brief_contract import validate_brief

_SOURCE = (
    "X公司用3个人做了200万ARR。他们砍掉了所有非核心功能，"
    "只保留付费转化最关键的一条路径。获客全靠SEO，成本几乎为零。"
)


def test_valid_brief_passes():
    brief = (
        "🎯 三人团队200万ARR的秘诀\n"
        "🏷 技术创业\n"
        "💡 砍掉所有非核心功能，只留付费转化路径。\n"
        "🗣 经验萃取\n"
        "▪️ 获客靠SEO，成本接近零。\n"
        "📡 信号萃取\n"
        "▪️ 小团队通过砍功能获得更高转化。\n"
        ""
        ""
        ""
        "💬 核心金句\n"
        "\"他们砍掉了所有非核心功能\"\n"
        "🛠 下一步\n"
        "▪️ 核验SEO获客成本。\n"
        "🔗 阅读原文: https://example.com/article"
    )
    assert validate_brief(brief) == []


def test_missing_url_is_rejected():
    brief = "💡 一句话判断，但没有链接。"
    errors = validate_brief(brief)
    assert any("URL" in e for e in errors)


def test_over_500_chars_is_rejected():
    brief = (
        "🎯 标题\n💡 判断\n📡 信号萃取\n"
        + ("长句子重复很多次。" * 80)
        + "\n🔗 https://example.com/a"
    )
    errors = validate_brief(brief)
    assert any("exceeds" in e for e in errors)


def test_missing_structured_section_is_rejected():
    brief = "🎯 标题\n💡 判断。\n🔗 https://example.com/a"
    errors = validate_brief(brief)
    assert any("missing required section" in e for e in errors)


def test_forbidden_filler_phrase_is_rejected():
    brief = (
        "🎯 标题\n💡 这篇文章主要讨论了SaaS。判断。\n"
        "📡 信号萃取\n"
        "🔗 https://example.com/a"
    )
    errors = validate_brief(brief)
    assert any("filler" in e for e in errors)




def test_short_structured_brief_is_allowed():
    # The contract allows <300 字 when the source is thin — padding is
    # forbidden, not brevity.
    brief = (
        "🎯 标题\n💡 短判断。\n📡 信号萃取\n▪️ 短信号。\n"
        "▪️ 信源: unknown\n"
        "🔗 https://example.com/a"
    )
    errors = validate_brief(brief)
    assert not any("exceeds" in e for e in errors)
