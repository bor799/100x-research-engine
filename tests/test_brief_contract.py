"""Tests for the WeChat brief hard contract."""

from __future__ import annotations

from knowledge_extractor_v3.brief_contract import validate_brief

_SOURCE = (
    "X公司用3个人做了200万ARR。他们砍掉了所有非核心功能，"
    "只保留付费转化最关键的一条路径。获客全靠SEO，成本几乎为零。"
)


def test_valid_brief_passes():
    brief = (
        "🎯 三人团队200万ARR的秘诀\n"
        "💡 砍掉所有非核心功能，只留付费转化路径。\n"
        "▪️ 获客靠SEO，成本接近零。\n"
        "💬 \"他们砍掉了所有非核心功能\"\n"
        "🔗 https://example.com/article"
    )
    assert validate_brief(brief, _SOURCE) == []


def test_missing_url_is_rejected():
    brief = "💡 一句话判断，但没有链接。"
    errors = validate_brief(brief, _SOURCE)
    assert any("URL" in e for e in errors)


def test_over_300_chars_is_rejected():
    brief = "💡 " + ("长句子重复很多次。" * 60) + "\n🔗 https://example.com/a"
    errors = validate_brief(brief, _SOURCE)
    assert any("exceeds" in e for e in errors)


def test_forbidden_section_marker_is_rejected():
    brief = "💡 判断。\n🏷 分类：创业\n🔗 https://example.com/a"
    errors = validate_brief(brief, _SOURCE)
    assert any("forbidden section" in e for e in errors)


def test_forbidden_filler_phrase_is_rejected():
    brief = "💡 这篇文章主要讨论了SaaS。判断。\n🔗 https://example.com/a"
    errors = validate_brief(brief, _SOURCE)
    assert any("filler" in e for e in errors)


def test_quote_not_in_source_is_rejected():
    brief = (
        "💡 判断。\n"
        "💬 \"这是一句原文里根本不存在的话用来测试\"\n"
        "🔗 https://example.com/a"
    )
    errors = validate_brief(brief, _SOURCE)
    assert any("not found verbatim" in e for e in errors)


def test_quote_locatable_via_whitespace_normalisation_passes():
    # Source has the text; brief quotes it with slightly different quotes.
    brief = (
        "💡 判断。\n"
        "💬 「他们砍掉了所有非核心功能」\n"
        "🔗 https://example.com/a"
    )
    assert validate_brief(brief, _SOURCE) == []


def test_short_brief_under_100_is_allowed():
    # The plan explicitly allows <100 字 when the source is thin — padding is
    # forbidden, not brevity.
    brief = "💡 短判断。\n🔗 https://example.com/a"
    errors = validate_brief(brief, _SOURCE)
    assert not any("exceeds" in e for e in errors)
