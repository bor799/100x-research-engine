"""Tests for daily report watchlist schema."""

from __future__ import annotations

import pytest

from knowledge_extractor_v3.daily_reports.system_files import write_default_system_files
from knowledge_extractor_v3.daily_reports.watchlist import (
    REQUIRED_WATCHLIST_FIELDS,
    load_watchlist,
    validate_watchlist_items,
)


def test_required_watchlist_fields_match_report_contract():
    assert REQUIRED_WATCHLIST_FIELDS == (
        "canonical_id",
        "ticker",
        "company",
        "market",
        "investment_status",
        "ai_bottleneck_category",
        "next_validation_signal",
    )


def test_default_watchlist_has_required_schema(tmp_path):
    write_default_system_files(tmp_path)
    items = load_watchlist(tmp_path / "WATCHLIST.yml")

    assert items
    for item in items:
        for field in REQUIRED_WATCHLIST_FIELDS:
            assert item[field]


def test_invalid_watchlist_reports_missing_fields():
    with pytest.raises(ValueError, match="missing required fields"):
        validate_watchlist_items([{"ticker": "NVDA"}])
