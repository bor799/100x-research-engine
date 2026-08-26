"""Local vault landing ledger for WeChat-pushed cards.

The outbox ``sent/`` directory only retains payloads for 14 days, and the
per-item Obsidian notes under ``AI进展/`` are indistinguishable from
archive-only content. This module closes that gap: every card that is
actually delivered to WeChat is also appended to a week-rolling markdown
ledger inside the vault, so pushed content stays visible in the same
week-based reading flow the user already reviews.

Layout (root = ``outputs.obsidian_root``)::

    <obsidian_root>/微信推送/微信推送-2026-08-W4.md   # one file per week

Week labels follow the vault's existing ``YYYY-MM-WN`` month-week
convention (W1..W5, ceil(day/7)) — the same pattern as the user's
``信息源/2026-08-W4`` folders. Entries are append-only and idempotent by
``event_id``; landing failures never raise (the WeChat receipt in the
outbox stays the source of truth for delivery).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .wechat_outbox import OutboxItem

LEDGER_SUBDIR = "微信推送"
LANE_LABELS = {"business": "商业故事", "strategic": "战略信号"}


@dataclass(frozen=True)
class LandOutcome:
    """Result of landing one outbox item into the vault ledger."""

    status: str  # landed | duplicate | error
    path: Path | None = None
    detail: str = ""


def week_label(day: date) -> str:
    """Month-based week label matching the vault's 2026-08-W4 convention."""
    week = (day.day + 6) // 7
    return f"{day.year:04d}-{day.month:02d}-W{week}"


class PushLedger:
    """Append sent cards to a week-rolling markdown file in the vault."""

    def __init__(
        self,
        root: Path,
        *,
        ledger_subdir: str = LEDGER_SUBDIR,
        notes_subdir: str = "AI进展",
    ) -> None:
        self.root = Path(root)
        self.ledger_subdir = ledger_subdir
        self.notes_subdir = notes_subdir

    # -- public --------------------------------------------------------

    def land(self, item: OutboxItem) -> LandOutcome:
        """Append one sent item to its week file; idempotent by event_id."""
        try:
            path = self.path_for(item)
            if path.exists() and f"event:{item.event_id}" in path.read_text(encoding="utf-8"):
                return LandOutcome(status="duplicate", path=path)
            self.ensure_header(item)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(self._render_entry(item))
            return LandOutcome(status="landed", path=path)
        except OSError as exc:
            return LandOutcome(status="error", detail=str(exc))

    def path_for(self, item: OutboxItem) -> Path:
        """Ledger file path derived from the item's send date."""
        day = _entry_date(item)
        name = f"微信推送-{week_label(day)}.md"
        return self.root / self.ledger_subdir / name

    # -- internals -----------------------------------------------------

    def _render_entry(self, item: OutboxItem) -> str:
        day = _entry_date(item)
        timestamp = (item.sent_at or item.created_at or "").replace("+00:00", "Z")
        clock = timestamp[11:16] or "--:--"
        heading = (item.text.splitlines() or ["(空卡片)"])[0].strip() or "(空卡片)"
        lane = LANE_LABELS.get(item.lane, item.lane)
        lines = [
            f"## {clock} {heading}",
            "",
            f"- 车道: {lane} · final_score: {item.final_score:.4f} · 来源: {item.source or '未知'}",
            f"- 原文: {item.url or '（无链接）'}",
        ]
        note = self._find_note(item.event_id)
        if note:
            lines.append(f"- 完整萃取: [[{note.stem}]]")
        lines.append(f"- (event:{item.event_id})")
        lines.extend(["", "> " + item.text.replace("\n", "\n> "), ""])
        return "\n".join(lines)

    def _find_note(self, event_id: str) -> Path | None:
        """Locate the AI进展 note whose filename ends with the event id.

        The pipeline names notes ``<date>-<slug>-<content_hash>.md`` and the
        outbox event_id is the content hash, so a suffix glob links the two.
        """
        if not event_id:
            return None
        notes = self.root / self.notes_subdir
        if not notes.is_dir():
            return None
        matches = sorted(notes.glob(f"*-{event_id}.md"))
        return matches[-1] if matches else None

    def ensure_header(self, item: OutboxItem) -> None:
        """Create the week file with frontmatter before the first append."""
        path = self.path_for(item)
        if path.exists():
            return
        label = week_label(_entry_date(item))
        header = (
            "---\n"
            f"title: 微信推送周记录（{label}）\n"
            "type: push-ledger\n"
            "generated_by: knowledge-extractor wechat_outbox\n"
            "---\n"
            "\n"
            f"# 微信推送 {label}\n"
            "\n"
            "本周实际送达微信的卡片，按发送时间落档；完整萃取笔记在"
            f" [[{self.notes_subdir}]] 目录（各条目内有链接）。\n"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")


def _entry_date(item: OutboxItem) -> date:
    """Week bucket comes from the send date (fallback: enqueue date)."""
    for stamp in (item.sent_at, item.created_at):
        try:
            return datetime.fromisoformat(stamp).date()
        except ValueError:
            continue
    return date.today()
