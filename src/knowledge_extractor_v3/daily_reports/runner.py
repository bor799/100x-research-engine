"""Runner for the US AI market daily report."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..config_loader import ConfigLoader
from .renderer import render_daily_report
from .system_files import write_default_system_files
from .watchlist import load_watchlist
from .weekly_output import daily_report_output_path


@dataclass(frozen=True)
class DailyReportRunResult:
    output_path: Path
    markdown: str
    wrote: bool


STOCK_CONTEXT_FILES = (
    "BOTTLENECK_3X_FRAMEWORK.md",
    "MINDSPACE_SOURCE_MCP_SOP.md",
    "watchlist_master.md",
    "company_score_table.md",
)

AI_TOPIC_KEYWORDS = (
    "AI",
    "人工智能",
    "深度学习",
    "大模型",
)

INFRASTRUCTURE_RELEVANCE_KEYWORDS = (
    "基础设施",
    "瓶颈",
    "算力",
    "数据中心",
    "GPU",
    "HBM",
    "AI网络",
    "交换机",
    "互连",
    "光通信",
    "电力",
    "AWS",
    "Azure",
    "Google Cloud",
    "OCI",
)

FINANCIAL_RELEVANCE_KEYWORDS = (
    "美股",
    "投资",
    "估值",
    "财报",
    "订单",
    "指引",
    "收入",
    "毛利率",
    "FCF",
    "backlog",
    "capex",
    "CapEx",
    "Fed",
    "利率",
)

HARD_RELEVANCE_KEYWORDS = (
    AI_TOPIC_KEYWORDS + INFRASTRUCTURE_RELEVANCE_KEYWORDS + FINANCIAL_RELEVANCE_KEYWORDS
)

COMPANY_RELEVANCE_KEYWORDS = (
    "NVDA",
    "Nvidia",
    "Microsoft",
    "Google",
    "Meta",
    "Amazon",
    "Oracle",
    "AMD",
    "Broadcom",
    "TSMC",
    "Apple",
)


def _append_line(path: Path, line: str, *, unique: bool = False) -> None:
    normalized = line.rstrip()
    if unique and path.exists():
        existing = path.read_text(encoding="utf-8").splitlines()
        if normalized in existing:
            return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(normalized + "\n")


def _append_manifest(output_path: Path, report_date: str) -> None:
    manifest_path = output_path.parent / "manifest.jsonl"
    entry = {
        "filename": output_path.name,
        "path": str(output_path),
        "report_type": "us_ai_market_daily",
        "date": report_date,
        "source_service": "v3_daily_report",
    }
    _append_line(manifest_path, json.dumps(entry, ensure_ascii=False, sort_keys=True), unique=True)


def _append_system_logs(
    system_dir: Path,
    report_date: str,
    signals: list[dict[str, object]],
) -> None:
    _append_line(
        system_dir / "DAILY_SIGNAL_LOG.md",
        f"| {report_date} | 待证据确认 | 待证据确认 | 待证据确认 | 0 | 0 | 数据源降级风险 | 明日继续验证 |",
        unique=True,
    )
    for signal in signals:
        _append_line(
            system_dir / "SIGNALS.md",
            "| {date} | {ticker} | {signal_type} | {evidence} | {action_label} | {follow_up} |".format(
                date=report_date,
                ticker=str(signal.get("ticker", "")),
                signal_type=str(signal.get("signal_type", "")),
                evidence=str(signal.get("source", "")),
                action_label=str(signal.get("action_label", "")),
                follow_up=str(signal.get("summary", "")),
            ),
            unique=True,
        )


def _snippet(text: str, *, limit: int = 140) -> str:
    lines: list[str] = []
    in_frontmatter = False
    for raw_line in text.splitlines():
        line = raw_line.strip().replace("|", "/")
        if line == "---":
            in_frontmatter = not in_frontmatter
            continue
        if in_frontmatter or not line or line.startswith("#"):
            continue
        lines.append(line)
        if len(" ".join(lines)) >= limit:
            break
    snippet = " ".join(lines)
    return snippet[:limit] if snippet else "待读取摘要"


def _title_from_markdown(path: Path, text: str) -> str:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("title:"):
            return line.split(":", 1)[1].strip().strip('"')
    return path.stem


def _keyword_present(keyword: str, haystack: str) -> bool:
    if keyword.isascii() and any(character.isalnum() for character in keyword):
        pattern = rf"(?<![A-Za-z0-9]){re.escape(keyword)}(?![A-Za-z0-9])"
        return re.search(pattern, haystack, flags=re.IGNORECASE) is not None
    return keyword in haystack


def _collect_recent_v3_notes(
    output_root: Path,
    *,
    lookback_hours: int,
    max_notes: int = 8,
) -> list[dict[str, str]]:
    if not output_root.exists():
        return []
    threshold = datetime.now().timestamp() - (lookback_hours * 3600)
    candidates: list[tuple[float, Path]] = []
    for path in output_root.rglob("*.md"):
        try:
            relative = path.relative_to(output_root)
        except ValueError:
            continue
        if "日报系统" in relative.parts or "日报" in relative.parts:
            continue
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            continue
        if modified_at >= threshold:
            candidates.append((modified_at, path))

    scored_notes: list[tuple[int, float, Path, str]] = []
    for modified_at, path in candidates:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        haystack = f"{path.name}\n{text}"
        ai_hits = sum(1 for keyword in AI_TOPIC_KEYWORDS if _keyword_present(keyword, haystack))
        infra_hits = sum(
            1 for keyword in INFRASTRUCTURE_RELEVANCE_KEYWORDS if _keyword_present(keyword, haystack)
        )
        financial_hits = sum(
            1 for keyword in FINANCIAL_RELEVANCE_KEYWORDS if _keyword_present(keyword, haystack)
        )
        company_hits = sum(
            1 for keyword in COMPANY_RELEVANCE_KEYWORDS if _keyword_present(keyword, haystack)
        )
        relevant = (
            (infra_hits >= 2 and ai_hits >= 1)
            or (infra_hits >= 1 and financial_hits >= 1)
            or (infra_hits >= 1 and ai_hits >= 1 and company_hits >= 1)
            or financial_hits >= 2
            or (financial_hits >= 1 and company_hits >= 1)
        )
        if not relevant:
            continue
        relevance = ai_hits + infra_hits + financial_hits + min(company_hits, 2)
        scored_notes.append((relevance, modified_at, path, text))

    notes: list[dict[str, str]] = []
    for _, _, path, text in sorted(scored_notes, key=lambda item: (item[0], item[1]), reverse=True)[:max_notes]:
        try:
            relative = path.relative_to(output_root)
        except ValueError:
            continue
        notes.append(
            {
                "file": str(relative),
                "title": _title_from_markdown(path, text),
                "snippet": _snippet(text),
            }
        )
    return notes


def _load_stock_contexts(stock_context_root: Path | None) -> list[dict[str, str]]:
    if stock_context_root is None:
        return []
    contexts: list[dict[str, str]] = []
    for filename in STOCK_CONTEXT_FILES:
        path = stock_context_root / filename
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        contexts.append(
            {
                "file": filename,
                "title": _title_from_markdown(path, text),
                "snippet": _snippet(text),
            }
        )
    return contexts


def _market_session_status(report_date: str) -> str:
    parsed = date.fromisoformat(report_date)
    if parsed.weekday() >= 5:
        return "非交易日/周末复盘"
    return "交易日或待交易所日历确认"


def run_us_ai_market_daily(
    *,
    report_date: str | None = None,
    output_root: Path | str,
    system_dir: Path | str,
    stock_context_root: Path | str | None = None,
    lookback_hours: int = 72,
    dry_run: bool = False,
    output_category: str = "日报",
) -> DailyReportRunResult:
    report_date = report_date or date.today().isoformat()
    output_root = Path(output_root)
    system_dir = Path(system_dir)
    resolved_stock_context_root = Path(stock_context_root) if stock_context_root else None
    write_default_system_files(system_dir, overwrite=False)

    watchlist_path = system_dir / "WATCHLIST.yml"
    watchlist_items = load_watchlist(watchlist_path)
    recent_notes = _collect_recent_v3_notes(output_root, lookback_hours=lookback_hours)
    stock_contexts = _load_stock_contexts(resolved_stock_context_root)
    signals = [
        {
            "ticker": str(item.get("ticker", "")),
            "signal_type": "需要后续追踪",
            "summary": str(item.get("next_validation_signal", "")),
            "action_label": "需要观察",
            "source": "WATCHLIST.yml",
        }
        for item in watchlist_items[:10]
    ]
    markdown = render_daily_report(
        report_date,
        signals=signals,
        source_status={
            "US market session": _market_session_status(report_date),
            "Mindspace MCP": "not checked by local runner; confidence downgraded",
            "V3 recent extracted notes": f"{len(recent_notes)} notes loaded",
            "Stock research context": f"{len(stock_contexts)}/{len(STOCK_CONTEXT_FILES)} files loaded",
        },
        recent_notes=recent_notes,
        stock_contexts=stock_contexts,
    )
    output_path = daily_report_output_path(
        output_root, report_date, category=output_category
    )

    if dry_run:
        return DailyReportRunResult(output_path=output_path, markdown=markdown, wrote=False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f".tmp-{output_path.stem}.md")
    tmp_path.write_text(markdown, encoding="utf-8")
    tmp_path.replace(output_path)
    _append_manifest(output_path, report_date)
    _append_system_logs(system_dir, report_date, signals)
    return DailyReportRunResult(output_path=output_path, markdown=markdown, wrote=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the US AI market daily report.")
    parser.add_argument("--date", dest="report_date", default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--system-dir", default=None)
    parser.add_argument("--config", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    loader = ConfigLoader(explicit_path=Path(args.config) if args.config else None)
    config = loader.load()
    daily_config = config.daily_reports.us_ai_market
    report_date = args.report_date or datetime.now(ZoneInfo(daily_config.timezone)).date().isoformat()
    output_root = Path(args.output_root) if args.output_root else loader.expand_path(config.outputs.obsidian_root)
    system_dir = Path(args.system_dir) if args.system_dir else loader.expand_path(daily_config.system_dir)
    stock_context_root = loader.expand_path(daily_config.stock_context_root)

    result = run_us_ai_market_daily(
        report_date=report_date,
        output_root=output_root,
        system_dir=system_dir,
        stock_context_root=stock_context_root,
        lookback_hours=daily_config.lookback_hours,
        dry_run=args.dry_run,
        output_category=daily_config.output_category,
    )
    print(result.output_path)
    if args.dry_run:
        print(result.markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
