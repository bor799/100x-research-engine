"""Tests for the US AI market daily report runner."""

from __future__ import annotations

from pathlib import Path

from knowledge_extractor_v3.daily_reports.runner import run_us_ai_market_daily
from knowledge_extractor_v3.daily_reports.system_files import write_default_system_files


def test_runner_dry_run_returns_target_path_without_writing(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    write_default_system_files(system_dir)

    result = run_us_ai_market_daily(
        report_date="2026-05-25",
        output_root=output_root,
        system_dir=system_dir,
        dry_run=True,
    )

    assert result.wrote is False
    assert result.output_path == output_root / "2026-05-W4" / "日报" / "2026-05-25_美股AI投资日报.md"
    assert not result.output_path.exists()
    assert "## 1. 今日信号" in result.markdown
    assert "## 2. 市场共识" in result.markdown
    assert "## 3. 踩坑点" in result.markdown


def test_runner_live_write_updates_report_and_logs(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    write_default_system_files(system_dir)

    result = run_us_ai_market_daily(
        report_date="2026-05-25",
        output_root=output_root,
        system_dir=system_dir,
        dry_run=False,
    )

    assert result.wrote is True
    assert result.output_path.exists()
    assert not list(result.output_path.parent.glob(".tmp-*"))
    assert (result.output_path.parent / "manifest.jsonl").exists()
    assert "2026-05-25" in (system_dir / "DAILY_SIGNAL_LOG.md").read_text(encoding="utf-8")
    assert "2026-05-25" in (system_dir / "SIGNALS.md").read_text(encoding="utf-8")


def test_runner_live_write_is_idempotent_for_signal_logs(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    write_default_system_files(system_dir)

    for _ in range(2):
        run_us_ai_market_daily(
            report_date="2026-05-25",
            output_root=output_root,
            system_dir=system_dir,
            dry_run=False,
        )

    signal_log = (system_dir / "DAILY_SIGNAL_LOG.md").read_text(encoding="utf-8")
    signal_ledger = (system_dir / "SIGNALS.md").read_text(encoding="utf-8")

    assert signal_log.count("| 2026-05-25 |") == 1
    assert signal_ledger.count("| 2026-05-25 | NVDA |") == 1


def test_runner_rejects_output_category_that_escapes_output_root(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    write_default_system_files(system_dir)

    try:
        run_us_ai_market_daily(
            report_date="2026-05-25",
            output_root=output_root,
            system_dir=system_dir,
            dry_run=False,
            output_category="../escaped",
        )
    except ValueError as exc:
        assert "output_category" in str(exc)
    else:
        raise AssertionError("expected escaping output_category to be rejected")


def test_weekend_report_marks_non_trading_day_review(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    write_default_system_files(system_dir)

    result = run_us_ai_market_daily(
        report_date="2026-05-24",
        output_root=output_root,
        system_dir=system_dir,
        dry_run=True,
    )

    assert "US market session: 非交易日/周末复盘" in result.markdown


def _write_stock_context(root: Path) -> None:
    root.mkdir(parents=True)
    for filename in (
        "BOTTLENECK_3X_FRAMEWORK.md",
        "MINDSPACE_SOURCE_MCP_SOP.md",
        "watchlist_master.md",
        "company_score_table.md",
    ):
        (root / filename).write_text(f"# {filename}\n\ncontext for {filename}\n", encoding="utf-8")


def test_runner_includes_recent_v3_notes_and_stock_context(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    stock_context_root = tmp_path / "股票上下文"
    write_default_system_files(system_dir)
    _write_stock_context(stock_context_root)
    note_dir = output_root / "2026-05-W4" / "财经"
    note_dir.mkdir(parents=True)
    (note_dir / "AI基础设施订单.md").write_text(
        "# AI基础设施订单\n\n硬证据需要继续验证。\n",
        encoding="utf-8",
    )

    result = run_us_ai_market_daily(
        report_date="2026-05-25",
        output_root=output_root,
        system_dir=system_dir,
        stock_context_root=stock_context_root,
        dry_run=True,
    )

    assert "## 附录：输入上下文" in result.markdown
    assert "AI基础设施订单" in result.markdown
    assert "BOTTLENECK_3X_FRAMEWORK.md" in result.markdown
    assert "company_score_table.md" in result.markdown


def test_runner_filters_irrelevant_recent_v3_notes(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    write_default_system_files(system_dir)
    note_dir = output_root / "2026-05-W4" / "AI进展"
    note_dir.mkdir(parents=True)
    (note_dir / "手机防盗机制.md").write_text(
        "# 手机防盗机制\n\n这是一条城市治安新闻。\n",
        encoding="utf-8",
    )
    (note_dir / "AI算力订单.md").write_text(
        "# AI算力订单\n\n数据中心 GPU 订单需要继续验证。\n",
        encoding="utf-8",
    )

    result = run_us_ai_market_daily(
        report_date="2026-05-25",
        output_root=output_root,
        system_dir=system_dir,
        dry_run=True,
    )

    assert "AI算力订单" in result.markdown
    assert "手机防盗机制" not in result.markdown


def test_runner_rejects_ai_notes_that_only_mention_no_investment_value(tmp_path):
    system_dir = tmp_path / "日报系统"
    output_root = tmp_path / "信息源"
    write_default_system_files(system_dir)
    note_dir = output_root / "2026-05-W4" / "AI进展"
    note_dir.mkdir(parents=True)
    (note_dir / "数学验证.md").write_text(
        "# 数学验证\n\nAI 工具辅助公式验证。本文无商业或投资利益冲突。\n",
        encoding="utf-8",
    )

    result = run_us_ai_market_daily(
        report_date="2026-05-25",
        output_root=output_root,
        system_dir=system_dir,
        dry_run=True,
    )

    assert "数学验证" not in result.markdown
