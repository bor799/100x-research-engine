"""Watchlist loading and validation for daily reports."""

from __future__ import annotations

from pathlib import Path

import yaml


REQUIRED_WATCHLIST_FIELDS = (
    "canonical_id",
    "ticker",
    "company",
    "market",
    "investment_status",
    "ai_bottleneck_category",
    "next_validation_signal",
)


def load_watchlist(path: Path | str) -> list[dict[str, object]]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("watchlist root must be a mapping")
    items = raw.get("watchlist", [])
    if not isinstance(items, list):
        raise ValueError("watchlist must be a list")
    validate_watchlist_items(items)
    return items


def validate_watchlist_items(items: list[object]) -> None:
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"watchlist item {index} must be a mapping")
        missing = [field for field in REQUIRED_WATCHLIST_FIELDS if not item.get(field)]
        if missing:
            raise ValueError(
                f"watchlist item {index} missing required fields: {', '.join(missing)}"
            )
