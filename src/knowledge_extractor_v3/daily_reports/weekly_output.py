"""Weekly output routing for high-cadence daily reports."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path


DateLike = date | datetime | str


def _coerce_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _safe_category_path(category: str) -> Path:
    path = Path(category)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("output_category must stay inside output_root")
    return path


def month_week(value: DateLike) -> str:
    """Return the vault month-bucket week label, e.g. 2026-05-W4."""
    report_date = _coerce_date(value)
    week = ((report_date.day - 1) // 7) + 1
    return f"{report_date:%Y-%m}-W{week}"


def weekly_output_subdir(value: DateLike, *, category: str = "日报") -> Path:
    return Path(month_week(value)) / _safe_category_path(category)


def daily_report_filename(value: DateLike) -> str:
    report_date = _coerce_date(value)
    return f"{report_date.isoformat()}_美股AI投资日报.md"


def daily_report_output_path(
    output_root: Path | str,
    value: DateLike,
    *,
    category: str = "日报",
) -> Path:
    root = Path(output_root)
    output_path = root / weekly_output_subdir(value, category=category) / daily_report_filename(value)
    root_resolved = root.resolve()
    if not output_path.resolve().is_relative_to(root_resolved):
        raise ValueError("output_category must stay inside output_root")
    return output_path
