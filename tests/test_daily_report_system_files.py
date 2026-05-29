"""Tests for daily report system file templates."""

from __future__ import annotations

import yaml

from knowledge_extractor_v3.daily_reports.system_files import (
    ACTION_LABELS,
    REQUIRED_SYSTEM_FILES,
    write_default_system_files,
)


def test_required_system_files_are_complete():
    assert REQUIRED_SYSTEM_FILES == (
        "DAILY_LOOP_PROMPT.md",
        "WATCHLIST.yml",
        "SIGNAL_DEFINITIONS.md",
        "DAILY_REPORT_TEMPLATE.md",
        "DATA_SOURCES.yaml",
        "RUNBOOK_DAILY_REPORT.md",
        "SIGNALS.md",
        "DAILY_SIGNAL_LOG.md",
        "DECISION_NOTES.md",
        "LOOP_PROMPT_US_AI_DAILY_DEV.md",
    )


def test_write_default_system_files(tmp_path):
    write_default_system_files(tmp_path)

    for name in REQUIRED_SYSTEM_FILES:
        assert (tmp_path / name).exists()


def test_yaml_templates_are_parseable(tmp_path):
    write_default_system_files(tmp_path)

    watchlist = yaml.safe_load((tmp_path / "WATCHLIST.yml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((tmp_path / "DATA_SOURCES.yaml").read_text(encoding="utf-8"))

    assert watchlist["schema_version"] == 1
    assert watchlist["watchlist"]
    assert sources["schema_version"] == 1
    assert sources["sources"]


def test_data_sources_distinguish_hard_sources_from_aihot(tmp_path):
    write_default_system_files(tmp_path)
    sources = yaml.safe_load((tmp_path / "DATA_SOURCES.yaml").read_text(encoding="utf-8"))
    source_names = {source["name"] for source in sources["sources"]}
    aihot = next(source for source in sources["sources"] if source["name"] == "AIHot RSS")

    assert aihot["conclusion_source"] is False
    for name in (
        "Company IR",
        "SEC EDGAR",
        "Exchange Filings",
        "CME FedWatch",
        "FRED",
        "U.S. Treasury",
        "EIA",
    ):
        assert name in source_names


def test_signal_definitions_include_all_action_labels(tmp_path):
    write_default_system_files(tmp_path)
    text = (tmp_path / "SIGNAL_DEFINITIONS.md").read_text(encoding="utf-8")

    assert len(ACTION_LABELS) == 16
    for label in ACTION_LABELS:
        assert label in text


def test_runbook_and_loop_prompt_capture_automation_boundaries(tmp_path):
    write_default_system_files(tmp_path)
    runbook = (tmp_path / "RUNBOOK_DAILY_REPORT.md").read_text(encoding="utf-8")
    loop_prompt = (tmp_path / "LOOP_PROMPT_US_AI_DAILY_DEV.md").read_text(encoding="utf-8")

    assert "08:03" in runbook
    assert "信息源/YYYY-MM-WN/日报/YYYY-MM-DD_美股AI投资日报.md" in runbook
    assert "不得迁移历史文件" in loop_prompt
    assert "不得改写 V3 主抓取流程" in loop_prompt
    assert "股票投资项目只读" in loop_prompt
    assert "Red test" in loop_prompt
    assert "Fix minimal implementation" in loop_prompt
    assert "Fortify with regression/docs" in loop_prompt
    assert "US_AI_DAILY_REPORT_DEV_COMPLETE" in loop_prompt
