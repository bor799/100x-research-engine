"""Markdown rendering for the US AI market daily report."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Mapping, Sequence

from .weekly_output import month_week


SIGNAL_TYPES = ("已验证信号", "待验证信号", "噪音信号", "需要后续追踪")


def _normalize_signal_type(signal_type: object) -> str:
    normalized = str(signal_type)
    return normalized if normalized in SIGNAL_TYPES else "需要后续追踪"


def _count_signals(signals: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts = {signal_type: 0 for signal_type in SIGNAL_TYPES}
    for signal in signals:
        counts[_normalize_signal_type(signal.get("signal_type", "需要后续追踪"))] += 1
    return counts


def _render_signal_table(signals: Sequence[Mapping[str, object]]) -> str:
    if not signals:
        return "| ticker | type | summary | action_label | source |\n|---|---|---|---|---|\n"

    rows = ["| ticker | type | summary | action_label | source |", "|---|---|---|---|---|"]
    for signal in signals:
        rows.append(
            "| {ticker} | {signal_type} | {summary} | {action_label} | {source} |".format(
                ticker=str(signal.get("ticker", "")),
                signal_type=str(signal.get("signal_type", "需要后续追踪")),
                summary=str(signal.get("summary", "")),
                action_label=str(signal.get("action_label", "需要观察")),
                source=str(signal.get("source", "")),
            )
        )
    return "\n".join(rows) + "\n"


def _render_source_status(source_status: Mapping[str, object] | None) -> str:
    if not source_status:
        return "- source status: not checked in renderer\n"
    return "\n".join(f"- {name}: {status}" for name, status in source_status.items()) + "\n"


def _render_context_table(items: Sequence[Mapping[str, object]]) -> str:
    if not items:
        return "| file | title | snippet |\n|---|---|---|\n"
    rows = ["| file | title | snippet |", "|---|---|---|"]
    for item in items:
        rows.append(
            "| {file} | {title} | {snippet} |".format(
                file=str(item.get("file", "")),
                title=str(item.get("title", "")),
                snippet=str(item.get("snippet", "")),
            )
        )
    return "\n".join(rows) + "\n"


def _render_grouped_signals(signals: Sequence[Mapping[str, object]]) -> str:
    blocks: list[str] = []
    for signal_type in SIGNAL_TYPES:
        grouped = [
            signal
            for signal in signals
            if _normalize_signal_type(signal.get("signal_type", "需要后续追踪")) == signal_type
        ]
        if not grouped:
            blocks.append(f"### {signal_type}\n\n- 暂无。\n")
            continue

        rows = ["| ticker | 信号 | 来源 | 动作标签 |", "|---|---|---|---|"]
        for signal in grouped:
            rows.append(
                "| {ticker} | {summary} | {source} | {action_label} |".format(
                    ticker=str(signal.get("ticker", "")),
                    summary=str(signal.get("summary", "")),
                    source=str(signal.get("source", "")),
                    action_label=str(signal.get("action_label", "需要观察")),
                )
            )
        blocks.append(f"### {signal_type}\n\n" + "\n".join(rows) + "\n")
    return "\n".join(blocks)


def _render_consensus(recent_notes: Sequence[Mapping[str, object]]) -> str:
    if not recent_notes:
        return (
            "- 今日没有足够的新近 V3 笔记形成新增共识。\n"
            "- 现有共识仍是：AI 投资判断要回到收入、订单、毛利率、CapEx 回报和供给瓶颈。\n"
            "- 在 live source 降级时，只把共识当作观察框架，不当作行动依据。\n"
        )

    rows = ["| 来源 | 可用共识 |", "|---|---|"]
    for note in recent_notes[:5]:
        rows.append(
            "| {title} | {snippet} |".format(
                title=str(note.get("title", "")),
                snippet=str(note.get("snippet", "")),
            )
        )
    rows.append("| 综合判断 | AI 主线需要继续用硬数据验证，尤其是需求、瓶颈、利润留存和拥挤度。 |")
    return "\n".join(rows) + "\n"


def _render_pitfalls(source_status: Mapping[str, object] | None) -> str:
    source_lines = []
    if source_status:
        source_lines = [
            f"{name}: {status}"
            for name, status in source_status.items()
            if "downgraded" in str(status).lower() or "not checked" in str(status).lower()
        ]
    source_warning = "；".join(source_lines) if source_lines else "当前信源状态需要继续核对。"
    return (
        f"- 信源降级：{source_warning}\n"
        "- 证据坑：不要把单日股价、社媒热度、AI 标签或 TAM 故事当成基本面验证。\n"
        "- 行动坑：watchlist 只提供观察优先级，不直接推出买入或卖出结论。\n"
        "- 叙事坑：如果没有收入、订单、毛利率、FCF、backlog 或 CapEx 回报支撑，先标为待验证。\n"
    )


def _render_next_watch(signals: Sequence[Mapping[str, object]]) -> str:
    if not signals:
        return "- 明日优先确认是否有公司披露、监管文件、财报或订单证据。\n"
    rows = ["| ticker | 明日观察 |", "|---|---|"]
    for signal in signals[:8]:
        rows.append(
            "| {ticker} | {summary} |".format(
                ticker=str(signal.get("ticker", "")),
                summary=str(signal.get("summary", "")),
            )
        )
    return "\n".join(rows) + "\n"


def render_daily_report(
    report_date: str,
    *,
    signals: Sequence[Mapping[str, object]] | None = None,
    source_status: Mapping[str, object] | None = None,
    recent_notes: Sequence[Mapping[str, object]] | None = None,
    stock_contexts: Sequence[Mapping[str, object]] | None = None,
    generated_at: str | None = None,
) -> str:
    signals = signals or []
    recent_notes = recent_notes or []
    stock_contexts = stock_contexts or []
    counts = _count_signals(signals)
    generated_at = generated_at or datetime.now(UTC).isoformat()
    week = month_week(report_date)

    frontmatter = "\n".join(
        [
            "---",
            f"date: {report_date}",
            f"week: {week}",
            "category: 日报",
            "report_type: us_ai_market_daily",
            f"generated_at: {generated_at}",
            "source_service: v3_daily_report",
            "signal_counts:",
            f"  已验证信号: {counts['已验证信号']}",
            f"  待验证信号: {counts['待验证信号']}",
            f"  噪音信号: {counts['噪音信号']}",
            f"  需要后续追踪: {counts['需要后续追踪']}",
            "---",
            "",
        ]
    )

    return (
        frontmatter
        + f"# 美股 AI 投资日报｜{report_date}\n\n"
        + "## 0. 信源状态\n\n"
        + _render_source_status(source_status)
        + "\n## 1. 今日信号\n\n"
        + f"- 已验证信号: {counts['已验证信号']}\n"
        + f"- 待验证信号: {counts['待验证信号']}\n"
        + f"- 噪音信号: {counts['噪音信号']}\n"
        + f"- 需要后续追踪: {counts['需要后续追踪']}\n\n"
        + _render_grouped_signals(signals)
        + "\n## 2. 市场共识\n\n"
        + _render_consensus(recent_notes)
        + "\n## 3. 踩坑点\n\n"
        + _render_pitfalls(source_status)
        + "\n## 4. 明日观察\n\n"
        + _render_next_watch(signals)
        + "\n## 附录：输入上下文\n\n"
        + "### V3 Recent Notes\n\n"
        + _render_context_table(recent_notes)
        + "\n### Stock Framework\n\n"
        + _render_context_table(stock_contexts)
        + "\n### Signal Ledger Input\n\n"
        + _render_signal_table(signals)
    )
