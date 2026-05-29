"""Tests for weekly Obsidian output routing."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from knowledge_extractor_v3.daily_reports.weekly_output import (
    daily_report_filename,
    daily_report_output_path,
    month_week,
    weekly_output_subdir,
)


def test_month_week_uses_month_bucket_rule():
    assert month_week(date(2026, 3, 24)) == "2026-03-W4"
    assert month_week(date(2026, 5, 25)) == "2026-05-W4"
    assert month_week(date(2026, 5, 1)) == "2026-05-W1"
    assert month_week(date(2026, 5, 31)) == "2026-05-W5"


def test_weekly_output_subdir_defaults_to_daily_category():
    assert weekly_output_subdir("2026-05-25") == Path("2026-05-W4") / "日报"


def test_daily_report_filename_is_stable():
    assert daily_report_filename("2026-05-25") == "2026-05-25_美股AI投资日报.md"


def test_daily_report_output_path_stays_under_root(tmp_path):
    output_path = daily_report_output_path(tmp_path, "2026-05-25")

    assert output_path == tmp_path / "2026-05-W4" / "日报" / "2026-05-25_美股AI投资日报.md"
