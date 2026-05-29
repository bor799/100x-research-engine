"""Tests for US AI market daily report rendering."""

from __future__ import annotations

from knowledge_extractor_v3.daily_reports.renderer import render_daily_report


def test_render_daily_report_contains_required_structure():
    markdown = render_daily_report(
        "2026-05-25",
        signals=[
            {
                "ticker": "NVDA",
                "signal_type": "已验证信号",
                "summary": "Data center margin evidence improved.",
                "action_label": "继续强势",
                "source": "company IR",
            },
            {
                "ticker": "NET",
                "signal_type": "待验证信号",
                "summary": "Workers AI adoption needs revenue proof.",
                "action_label": "需要观察",
                "source": "V3 extracted note",
            },
            {
                "ticker": "TSLA",
                "signal_type": "噪音信号",
                "summary": "Single-day social hype without operating data.",
                "action_label": "叙事未验证",
                "source": "social",
            },
        ],
        source_status={"Mindspace MCP": "degraded", "V3": "available"},
    )

    assert markdown.startswith("---")
    assert "date: 2026-05-25" in markdown
    assert "week: 2026-05-W4" in markdown
    assert "category: 日报" in markdown
    assert "report_type: us_ai_market_daily" in markdown
    assert "已验证信号: 1" in markdown
    assert "待验证信号: 1" in markdown
    assert "噪音信号: 1" in markdown
    assert "## 0. 信源状态" in markdown
    assert "## 1. 今日信号" in markdown
    assert "## 2. 市场共识" in markdown
    assert "## 3. 踩坑点" in markdown
    assert "## 4. 明日观察" in markdown
    assert "## 附录：输入上下文" in markdown
    assert "### 已验证信号" in markdown
    assert "### 待验证信号" in markdown
    assert "### 噪音信号" in markdown
    assert "### 需要后续追踪" in markdown
    assert "不要把单日股价" in markdown


def test_render_daily_report_avoids_direct_trade_words():
    markdown = render_daily_report("2026-05-25", signals=[])

    assert "直接买入" not in markdown
    assert "直接卖出" not in markdown
