"""Periodic vault dedup: merge duplicate article files, restore orphans.

Groups are keyed by frontmatter ``article_id`` (the content hash). The
oldest path-sorted copy is canonical — the same file ``MagazineStore.find``
has always written user feedback to — and losers move into the per-week
``.trash-dedup/`` directory that ``scan_articles``' glob never sees. A group
is the guarantee that the last copy of an article is never trashed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .updates import UPDATES_END, UPDATES_START, record_manifest_event
from .vault_index import VaultIndex, _read_frontmatter

SHANGHAI = ZoneInfo("Asia/Shanghai")
TRASH_DIRNAME = ".trash-dedup"
PLACEHOLDER_FEEDBACK = {"尚未提交评论。", "已读，无评论。", ""}
# The default ai-review block ships with a single HTML comment placeholder.
PLACEHOLDER_REVIEW_MARKER = "提交评论后，由 100X 定向萃取服务写入"

_MANAGED_BLOCKS = (
    ("user-feedback", f"<!-- 100x:user-feedback:start -->", f"<!-- 100x:user-feedback:end -->", True),
    ("ai-review", f"<!-- 100x:ai-review:start -->", f"<!-- 100x:ai-review:end -->", False),
    ("updates", UPDATES_START, UPDATES_END, False),
)


@dataclass(frozen=True)
class DedupeGroup:
    article_id: str
    canonical: Path
    losers: list[Path]


@dataclass
class DedupeReport:
    """Mutable accumulator: dedupe_vault folds per-group results in as it goes."""

    restored: list[str] = field(default_factory=list)
    merged_groups: int = 0
    trashed_files: list[str] = field(default_factory=list)
    state_migrations: int = 0
    weeks_rebuilt: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def scan_groups(root: Path) -> list[DedupeGroup]:
    index = VaultIndex(root)
    index.rebuild()
    groups: list[DedupeGroup] = []
    for article_id, refs in index.by_id.items():
        if len(refs) > 1:
            groups.append(DedupeGroup(
                article_id=article_id,
                canonical=refs[0].path,
                losers=[ref.path for ref in refs[1:]],
            ))
    return groups


def _block_inner(text: str, start: str, end: str) -> str:
    marker = text.find(start)
    if marker < 0:
        return ""
    begin = marker + len(start)
    finish = text.find(end, begin)
    return text[begin:finish].strip() if finish >= 0 else ""


def _is_placeholder(inner: str, *, feedback_style: bool) -> bool:
    stripped = inner.strip()
    if not stripped:
        return True
    if PLACEHOLDER_REVIEW_MARKER in stripped and len(stripped) < 200:
        return True
    if feedback_style:
        # A feedback block that is only the default lines.
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        non_heading = [line for line in lines if not line.startswith("#")]
        return all(line in PLACEHOLDER_FEEDBACK for line in non_heading)
    return False


def _merge_block_inner(canonical_inner: str, loser_inner: str) -> str:
    if not loser_inner:
        return canonical_inner
    if not canonical_inner:
        return loser_inner
    if canonical_inner.strip() == loser_inner.strip():
        return canonical_inner
    separator = f"<!-- 合并自重复条目 {datetime.now(SHANGHAI).date().isoformat()} -->"
    return canonical_inner.rstrip() + "\n\n" + separator + "\n\n" + loser_inner.strip()


def _rewrite_with_blocks(path: Path, text: str, replacements: dict[tuple[str, str], str]) -> str:
    """Substitute managed block inners in ``text``; returns the updated text."""
    for (start, end), inner in replacements.items():
        marker = text.find(start)
        if marker < 0:
            continue
        begin = marker + len(start)
        finish = text.find(end, begin)
        if finish < 0:
            continue
        updated = f"{start}\n{inner.strip()}\n{end}" if inner.strip() else f"{start}\n{end}"
        text = text[:marker] + updated + text[finish + len(end):]
    return text


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _trash_path(loser: Path) -> Path:
    trash_dir = loser.parent / TRASH_DIRNAME
    trash_dir.mkdir(parents=True, exist_ok=True)
    target = trash_dir / loser.name
    suffix = 1
    while target.exists():
        target = trash_dir / f"{loser.stem}-{suffix}{loser.suffix}"
        suffix += 1
    return target


def _merge_state_across_weeks(root: Path, group: DedupeGroup) -> int:
    """Fold foreign-week state entries into the canonical week's entry."""
    from ..magazine import MagazineStore

    canonical_week = group.canonical.parent.name
    store = MagazineStore(root)
    migrations = 0
    foreign_weeks = sorted({loser.parent.name for loser in group.losers} - {canonical_week})
    for week in foreign_weeks:
        try:
            canonical_data = store.load_week(canonical_week)
            foreign_data = store.load_week(week)
        except Exception:
            continue
        canonical_entries = canonical_data.setdefault("articles", {})
        foreign_entries = foreign_data.setdefault("articles", {})
        if not isinstance(canonical_entries, dict) or not isinstance(foreign_entries, dict):
            continue
        foreign_state = foreign_entries.get(group.article_id)
        if not isinstance(foreign_state, dict):
            continue
        canonical_state = canonical_entries.get(group.article_id)
        if not isinstance(canonical_state, dict):
            foreign_state["week"] = canonical_week
            canonical_entries[group.article_id] = foreign_state
            del foreign_entries[group.article_id]
            store.save_week(canonical_week, canonical_data)
            store.save_week(week, foreign_data)
            migrations += 1
            continue
        merged = _merge_state_fields(canonical_state, foreign_state)
        canonical_entries[group.article_id] = merged
        del foreign_entries[group.article_id]
        store.save_week(canonical_week, canonical_data)
        store.save_week(week, foreign_data)
        migrations += 1
    return migrations


def _merge_state_fields(canonical: dict[str, object], foreign: dict[str, object]) -> dict[str, object]:
    merged = dict(canonical)

    def prefer(key: str, is_better) -> None:
        other = foreign.get(key)
        if other is not None and (merged.get(key) in (None, "", []) or is_better(merged.get(key), other)):
            merged[key] = other

    prefer("read_at", lambda a, b: False)
    prefer("disposition", lambda a, b: False)
    prefer("comment", lambda a, b: bool(str(b or "").strip()) and not str(a or "").strip())
    merged["update_pending"] = bool(canonical.get("update_pending")) or bool(foreign.get("update_pending"))

    annotations = list(canonical.get("annotations") or [])
    for item in foreign.get("annotations") or []:
        if isinstance(item, dict) and item.get("id") not in {a.get("id") for a in annotations if isinstance(a, dict)}:
            annotations.append(item)
    merged["annotations"] = annotations

    updates = list(canonical.get("updates") or [])
    seen_hashes = {
        item.get("content_hash") for item in updates if isinstance(item, dict)
    }
    for item in foreign.get("updates") or []:
        if isinstance(item, dict) and item.get("content_hash") not in seen_hashes:
            updates.append(item)
            seen_hashes.add(item.get("content_hash"))
    merged["updates"] = updates

    canonical_review = canonical.get("review") if isinstance(canonical.get("review"), dict) else {}
    foreign_review = foreign.get("review") if isinstance(foreign.get("review"), dict) else {}
    if str(foreign_review.get("result") or "") and not str(canonical_review.get("result") or ""):
        merged["review"] = foreign_review
    elif int(foreign_review.get("revision") or 0) > int(canonical_review.get("revision") or 0):
        merged["review"] = foreign_review
    return merged


def restore_orphans(root: Path, *, dry_run: bool = False) -> list[Path]:
    """Move trashed files whose article_id has no live copy back to the week."""
    root = Path(root).resolve()
    index = VaultIndex(root)
    index.rebuild()
    restored: list[Path] = []
    for path in sorted(root.glob(f"????-??-W?/{TRASH_DIRNAME}/*.md")):
        metadata = _read_frontmatter(path)
        article_id = str(metadata.get("article_id") or "").strip()
        if not article_id or article_id in index.by_id:
            continue
        target = path.parent.parent / path.name
        if target.exists():
            continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(path, target)
            record_manifest_event(path.parent.parent, {
                "kind": "restored",
                "filename": path.name,
                "article_id": article_id,
            })
        restored.append(target)
    return restored


def dedupe_vault(root: Path, *, dry_run: bool = False) -> DedupeReport:
    root = Path(root).resolve()
    report = DedupeReport()
    report.restored.extend(str(p) for p in restore_orphans(root, dry_run=dry_run))

    weeks_rebuilt: set[str] = set()
    for group in scan_groups(root):
        try:
            texts = {p: p.read_text(encoding="utf-8") for p in [group.canonical, *group.losers]}
        except OSError as exc:
            report.errors.append(f"{group.article_id}: unreadable file: {exc}")
            continue

        canonical_text = texts[group.canonical]
        replacements: dict[tuple[str, str], str] = {}
        for _name, start, end, feedback_style in _MANAGED_BLOCKS:
            canonical_inner = _block_inner(canonical_text, start, end)
            loser_inner = ""
            for loser in group.losers:
                candidate = _block_inner(texts[loser], start, end)
                if _is_placeholder(candidate, feedback_style=feedback_style):
                    continue
                loser_inner = _merge_block_inner(loser_inner, candidate)
            if not loser_inner:
                continue
            if _is_placeholder(canonical_inner, feedback_style=feedback_style):
                replacements[(start, end)] = loser_inner
            else:
                replacements[(start, end)] = _merge_block_inner(canonical_inner, loser_inner)

        if replacements:
            updated = _rewrite_with_blocks(group.canonical, canonical_text, replacements)
            if not dry_run:
                _atomic_write(group.canonical, updated)
        if not dry_run:
            report.state_migrations += _merge_state_across_weeks(root, group)
        else:
            foreign = {loser.parent.name for loser in group.losers} - {group.canonical.parent.name}
            report.state_migrations += len(foreign)
        report.merged_groups += 1

        for loser in group.losers:
            weeks_rebuilt.add(loser.parent.name)
            if dry_run:
                report.trashed_files.append(str(loser))
                continue
            target = _trash_path(loser)
            os.replace(loser, target)
            record_manifest_event(loser.parent, {
                "kind": "file_merged",
                "filename": loser.name,
                "article_id": group.article_id,
                "canonical": group.canonical.relative_to(root).as_posix(),
            })
            report.trashed_files.append(str(target))
        weeks_rebuilt.add(group.canonical.parent.name)

    weeks_rebuilt.add(_current_week_safe())
    if not dry_run:
        from ..magazine import build_issue

        for week in sorted(weeks_rebuilt):
            try:
                build_issue(root, week)
                report.weeks_rebuilt.append(week)
            except Exception as exc:
                report.errors.append(f"rebuild {week}: {exc}")
    else:
        report.weeks_rebuilt = sorted(weeks_rebuilt)
    return report


def _current_week_safe() -> str:
    from ..magazine import current_week

    return current_week()
