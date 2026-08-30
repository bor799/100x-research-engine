"""Periodic vault dedup: duplicate groups, cross-week state, orphan restore."""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_extractor_v3.magazine import (
    FEEDBACK_END,
    FEEDBACK_START,
    scan_articles,
)
from knowledge_extractor_v3.outputs.dedupe import (
    TRASH_DIRNAME,
    dedupe_vault,
    restore_orphans,
    scan_groups,
)

W4 = "2026-08-W4"
W5 = "2026-08-W5"


def _managed_md(
    article_id: str,
    title: str,
    url: str,
    text: str,
    *,
    feedback: str = "尚未提交评论。",
) -> str:
    return f"""---
type: "knowledge-extract"
article_id: "{article_id}"
title: "{title}"
source: "rss"
processed_at: "2026-08-30T10:00:00+08:00"
final_score: 7.8
signal_tier: "A"
url: "{url}"
---

# {title} 存档简报

{FEEDBACK_START}
## 阅读反馈

{feedback}
{FEEDBACK_END}

<!-- 100x:ai-review:start -->
<!-- 提交评论后，由 100X 定向萃取服务写入。 -->
<!-- 100x:ai-review:end -->

## 原文

{text}
"""


def _write_article(
    root: Path,
    *,
    week: str,
    date: str,
    title: str,
    article_id: str,
    url: str,
    text: str = "一篇足够长的正文内容。" * 30,
    feedback: str = "尚未提交评论。",
) -> Path:
    week_dir = root / week
    week_dir.mkdir(parents=True, exist_ok=True)
    path = week_dir / f"{date} {title} {article_id[:8]}.md"
    path.write_text(
        _managed_md(article_id, title, url, text, feedback=feedback),
        encoding="utf-8",
    )
    return path


def _manifest_events(week_dir: Path) -> list[dict[str, object]]:
    manifest = week_dir / "manifest.jsonl"
    if not manifest.exists():
        return []
    return [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_state(root: Path, week: str, article_id: str, state: dict[str, object]) -> None:
    week_dir = root / week
    week_dir.mkdir(parents=True, exist_ok=True)
    path = week_dir / f"阅读状态 {week}.json"
    data: dict[str, object] = {"week": week, "updated_at": "", "articles": {}}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.setdefault("articles", {})
    entries[article_id] = state
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_same_week_duplicate_keeps_oldest_and_merges_user_content(tmp_path):
    article_id = "a" * 64
    url = "https://example.com/dup"
    canonical = _write_article(
        tmp_path,
        week=W4,
        date="2026-08-26",
        title="第一次落档",
        article_id=article_id,
        url=url,
        feedback="正本上的既有批注。",
    )
    loser = _write_article(
        tmp_path,
        week=W4,
        date="2026-08-27",
        title="重复标题击穿幂等",
        article_id=article_id,
        url=url,
        feedback="重复文件上的真实批注。",
    )

    report = dedupe_vault(tmp_path)

    assert report.merged_groups == 1
    assert len(report.trashed_files) == 1
    assert canonical.exists()
    assert not loser.exists()
    assert (tmp_path / W4 / TRASH_DIRNAME / loser.name).exists()

    # Both real feedbacks survive, joined by the merge separator.
    canonical_text = canonical.read_text(encoding="utf-8")
    assert "正本上的既有批注。" in canonical_text
    assert "重复文件上的真实批注。" in canonical_text
    assert canonical_text.index("正本上的既有批注。") < canonical_text.index("重复文件上的真实批注。")
    assert "合并自重复条目" in canonical_text

    events = _manifest_events(tmp_path / W4)
    assert any(e.get("kind") == "file_merged" for e in events)

    assert len(scan_articles(tmp_path)) == 1


def test_second_run_reports_nothing_and_writes_no_events(tmp_path):
    article_id = "b" * 64
    url = "https://example.com/idem"
    _write_article(tmp_path, week=W4, date="2026-08-26", title="正本", article_id=article_id, url=url)
    _write_article(tmp_path, week=W4, date="2026-08-27", title="副本", article_id=article_id, url=url)

    dedupe_vault(tmp_path)
    events_before = _manifest_events(tmp_path / W4)
    trash_before = sorted(p.name for p in (tmp_path / W4 / TRASH_DIRNAME).glob("*.md"))

    report = dedupe_vault(tmp_path)

    assert report.merged_groups == 0
    assert report.trashed_files == []
    assert _manifest_events(tmp_path / W4) == events_before
    assert sorted(p.name for p in (tmp_path / W4 / TRASH_DIRNAME).glob("*.md")) == trash_before


def test_single_article_is_never_touched(tmp_path):
    path = _write_article(
        tmp_path, week=W4, date="2026-08-26", title="唯一", article_id="c" * 64, url="https://example.com/one"
    )
    before = path.read_text(encoding="utf-8")

    report = dedupe_vault(tmp_path)

    assert report.merged_groups == 0
    assert report.trashed_files == []
    assert path.read_text(encoding="utf-8") == before
    assert not (tmp_path / W4 / TRASH_DIRNAME).exists()


def test_unreadable_canonical_skips_whole_group(tmp_path):
    """A file whose frontmatter cannot be read is invisible to the index, so
    no group forms and nothing is ever moved on a half view."""
    article_id = "d" * 64
    url = "https://example.com/broken"
    canonical = _write_article(tmp_path, week=W4, date="2026-08-26", title="不可读", article_id=article_id, url=url)
    loser = _write_article(tmp_path, week=W4, date="2026-08-27", title="副本", article_id=article_id, url=url)
    canonical.chmod(0o000)
    try:
        report = dedupe_vault(tmp_path)
    finally:
        canonical.chmod(0o644)

    assert report.merged_groups == 0
    assert report.trashed_files == []
    assert canonical.exists() and loser.exists()  # nothing moved on a half view
    assert not (tmp_path / W4 / TRASH_DIRNAME).exists()


def test_cross_week_duplicate_migrates_reading_state(tmp_path):
    article_id = "e" * 64
    url = "https://example.com/cross"
    canonical = _write_article(
        tmp_path, week=W4, date="2026-08-26", title="跨周正本", article_id=article_id, url=url
    )
    loser = _write_article(
        tmp_path,
        week=W5,
        date="2026-08-30",
        title="跨周副本",
        article_id=article_id,
        url=url,
        feedback="跨周文件上的评论。",
    )
    _write_state(
        tmp_path,
        W5,
        article_id,
        {
            "article_id": article_id,
            "week": W5,
            "path": loser.relative_to(tmp_path).as_posix(),
            "read_at": "2026-08-30T09:00:00",
            "disposition": "no_comment",
            "comment": "",
            "annotations": [{"id": "a1", "quote": "关键句", "comment": "划线"}],
        },
    )

    report = dedupe_vault(tmp_path)

    assert report.state_migrations == 1
    assert not loser.exists()
    assert (tmp_path / W5 / TRASH_DIRNAME / loser.name).exists()
    # The reading state followed the canonical file into W4.
    w4_state = json.loads((tmp_path / W4 / f"阅读状态 {W4}.json").read_text(encoding="utf-8"))
    assert article_id in w4_state["articles"]
    assert w4_state["articles"][article_id]["read_at"] == "2026-08-30T09:00:00"
    assert w4_state["articles"][article_id]["annotations"][0]["quote"] == "关键句"
    w5_state = json.loads((tmp_path / W5 / f"阅读状态 {W5}.json").read_text(encoding="utf-8"))
    assert article_id not in w5_state["articles"]
    # User content survived in the canonical markdown.
    assert "跨周文件上的评论。" in canonical.read_text(encoding="utf-8")


def test_trashed_orphan_without_live_copy_is_restored(tmp_path):
    live_id = "f" * 64
    orphan_id = "0" * 64
    _write_article(tmp_path, week=W5, date="2026-08-30", title="幸存者", article_id=live_id, url="https://example.com/live")
    orphan = _write_article(tmp_path, week=W5, date="2026-08-29", title="孤儿", article_id=orphan_id, url="https://example.com/orphan")
    trash_dir = tmp_path / W5 / TRASH_DIRNAME
    trash_dir.mkdir(parents=True, exist_ok=True)
    orphan.rename(trash_dir / orphan.name)
    # A trash copy whose article still has a live file stays trashed.
    shadow = _write_article(tmp_path, week=W5, date="2026-08-28", title="影子", article_id=live_id, url="https://example.com/live")
    shadow.rename(trash_dir / shadow.name)

    restored = restore_orphans(tmp_path)

    assert restored == [tmp_path / W5 / orphan.name]
    assert (tmp_path / W5 / orphan.name).exists()
    assert (trash_dir / shadow.name).exists()
    assert any(e.get("kind") == "restored" for e in _manifest_events(tmp_path / W5))
    # dedupe_vault runs the same restore first; the shadow stays trashed
    # because its article still has a live copy, so no group forms.
    report = dedupe_vault(tmp_path)
    assert [str(p) for p in report.restored] == []  # already restored
    assert report.merged_groups == 0


def test_dry_run_computes_but_touches_nothing(tmp_path):
    article_id = "9" * 64
    url = "https://example.com/dry"
    _write_article(tmp_path, week=W4, date="2026-08-26", title="正本", article_id=article_id, url=url)
    loser = _write_article(tmp_path, week=W4, date="2026-08-27", title="副本", article_id=article_id, url=url)

    report = dedupe_vault(tmp_path, dry_run=True)

    assert report.merged_groups == 1
    assert len(report.trashed_files) == 1
    assert loser.exists()  # nothing moved
    assert not (tmp_path / W4 / TRASH_DIRNAME).exists()
    assert not (tmp_path / W4 / "manifest.jsonl").exists()


def test_scan_groups_orders_oldest_first(tmp_path):
    article_id = "8" * 64
    url = "https://example.com/order"
    _write_article(tmp_path, week=W4, date="2026-08-27", title="较新", article_id=article_id, url=url)
    oldest = _write_article(tmp_path, week=W4, date="2026-08-26", title="较旧", article_id=article_id, url=url)

    groups = scan_groups(tmp_path)

    assert len(groups) == 1
    assert groups[0].canonical == oldest.resolve()
    assert len(groups[0].losers) == 1
