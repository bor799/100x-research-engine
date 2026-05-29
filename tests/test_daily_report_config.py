"""Tests for daily report configuration."""

from __future__ import annotations

from pathlib import Path

from knowledge_extractor_v3.config_loader import ConfigLoader


def _make_project(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    (config_dir / "config.local.yaml").write_text(content, encoding="utf-8")
    return tmp_path


def test_us_ai_market_daily_report_defaults(tmp_path):
    project = _make_project(tmp_path, "runtime:\n  state_root: /tmp/state\n")
    loader = ConfigLoader(project_root=project)
    config = loader.load()

    daily = config.daily_reports.us_ai_market
    assert daily.enabled is False
    assert daily.timezone == "Asia/Shanghai"
    assert daily.schedule_time == "08:03"
    assert daily.system_dir == "~/Documents/Obsidian Vault/信息源/日报系统"
    assert daily.stock_context_root == (
        "~/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览"
    )
    assert daily.output_category == "日报"
    assert daily.lookback_hours == 72
    assert daily.non_trading_day_mode == "review"


def test_us_ai_market_daily_report_explicit_config(tmp_path):
    project = _make_project(
        tmp_path,
        "\n".join(
            [
                "runtime:",
                "  state_root: /tmp/state",
                "daily_reports:",
                "  us_ai_market:",
                "    enabled: true",
                "    timezone: America/New_York",
                "    schedule_time: \"07:30\"",
                "    system_dir: daily-system",
                "    stock_context_root: stock-context",
                "    output_category: MarketDaily",
                "    lookback_hours: 24",
                "    non_trading_day_mode: skip",
            ]
        ),
    )
    loader = ConfigLoader(project_root=project)
    config = loader.load()

    daily = config.daily_reports.us_ai_market
    assert daily.enabled is True
    assert daily.timezone == "America/New_York"
    assert daily.schedule_time == "07:30"
    assert loader.expand_path(daily.system_dir) == (project / "daily-system").resolve()
    assert loader.expand_path(daily.stock_context_root) == (project / "stock-context").resolve()
    assert daily.output_category == "MarketDaily"
    assert daily.lookback_hours == 24
    assert daily.non_trading_day_mode == "skip"
