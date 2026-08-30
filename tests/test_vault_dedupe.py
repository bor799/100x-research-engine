"""Write-time dedup guard and the vault frontmatter index.

The guard is the vault-layer backstop for the production P0-1 bug: a queue
race reprocessed a task and the LLM-authored filename bypassed the exists()
idempotency, forking one article into two files. These tests pin the guard's
three behaviours: suppress a foreign-path second write, keep the same-path
idempotent rewrite, and write fresh when the canonical file is gone.
"""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_extractor_v3.models import ExtractionResult, FetchedContent, ScoreResult
from knowledge_extractor_v3.outputs.live_obsidian import LiveObsidianWriter
from knowledge_extractor_v3.outputs.vault_index import VaultIndex

WEEK = "2026-08-W4"


def _content(article_id: str = "abc123def456", *, url: str = "https://example.com/article") -> FetchedContent:
    return FetchedContent(
        url=url,
        source="fixture",
        source_type="web_article",
        title="原始标题",
        text="# 原始标题\n\n这是抓取后的完整原文。",
        fetched_at="2026-08-28T00:00:00+00:00",
        content_hash=article_id,
    )


def _score() -> ScoreResult:
    return ScoreResult(
        prompt_bundle="p", prompt_hash="h", model_route="stub", raw_text="{}", parsed={},
        score=8.0, final_score=.8, signal_tier="A", information_gain=.8,
        action_value=.7, relevance=.9, rationale="test", is_spam=False,
    )


def _extraction(title: str = "压缩后的中文标题") -> ExtractionResult:
    return ExtractionResult(
        prompt_bundle="p", prompt_hash="h", model_route="stub", raw_text="{}", parsed={},
        title=title, one_line_signal="一句话",
        obsidian_brief_markdown=f"# {title}\n\n- 判断一",
    )


def _write(
    tmp_path: Path,
    article_id: str = "abc123def456",
    title: str = "压缩后的中文标题",
    *,
    url: str = "https://example.com/article",
    dedup_guard: bool = True,
) -> Path | object:
    writer = LiveObsidianWriter(tmp_path, write_manifest=True, dedup_guard=dedup_guard)
    result = writer.write(
        _content(article_id, url=url), _score(), _extraction(title),
        prompt_bundle="p", prompt_hash="h",
    )
    return result


def test_second_write_same_hash_is_suppressed_to_canonical(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "knowledge_extractor_v3.outputs.live_obsidian.utc_now",
        lambda: "2026-08-28T00:30:00+00:00",
    )
    first = Path(_write(tmp_path))
    # Same article_id, different LLM-authored title: the exact queue-race shape.
    second = _write(tmp_path, title="换了标题的重复萃取")

    assert second == str(first)
    week_files = sorted((tmp_path / WEEK).glob("*.md"))
    assert len(week_files) == 1

    manifest = tmp_path / WEEK / "manifest.jsonl"
    assert manifest.exists()
    events = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    suppressed = [e for e in events if e.get("event") == "dedup" and e.get("kind") == "write_suppressed"]
    assert suppressed and suppressed[0]["article_id"] == "abc123def456"


def test_same_path_rewrite_stays_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "knowledge_extractor_v3.outputs.live_obsidian.utc_now",
        lambda: "2026-08-28T00:30:00+00:00",
    )
    first = Path(_write(tmp_path))
    body = first.read_text(encoding="utf-8")
    import re

    injected = re.sub(
        r"<!-- 100x:user-feedback:start -->.*?<!-- 100x:user-feedback:end -->",
        "<!-- 100x:user-feedback:start -->\\n## 阅读反馈\\n\\n这是我的判断。\\n<!-- 100x:user-feedback:end -->",
        body,
        count=1,
        flags=re.S,
    )
    assert injected != body, "fixture must actually inject feedback into the block"
    first.write_text(injected, encoding="utf-8")

    second = _write(tmp_path)  # same hash, same title -> same final path

    assert Path(second) == first
    assert "这是我的判断。" in first.read_text(encoding="utf-8")
    assert len(list((tmp_path / WEEK).glob("*.md"))) == 1


def test_guard_disabled_keeps_legacy_double_write(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "knowledge_extractor_v3.outputs.live_obsidian.utc_now",
        lambda: "2026-08-28T00:30:00+00:00",
    )
    _write(tmp_path, dedup_guard=False)
    _write(tmp_path, title="换了标题的重复萃取", dedup_guard=False)

    assert len(list((tmp_path / WEEK).glob("*.md"))) == 2


def test_missing_canonical_writes_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "knowledge_extractor_v3.outputs.live_obsidian.utc_now",
        lambda: "2026-08-28T00:30:00+00:00",
    )
    first = Path(_write(tmp_path))
    first.unlink()

    second = Path(_write(tmp_path, title="重建后的标题"))

    assert second.exists() and second != first or not first.exists()
    assert second.exists()


def test_index_ignores_unmanaged_files_and_trash(tmp_path):
    week = tmp_path / WEEK
    week.mkdir(parents=True)
    (week / "managed.md").write_text(
        "---\ntype: knowledge-extract\narticle_id: hash1\nurl: https://example.com/a\n---\n正文",
        encoding="utf-8",
    )
    (week / "个人笔记.md").write_text("---\ntitle: 随手记\n---\n与系统无关", encoding="utf-8")
    trash = week / ".trash-dedup"
    trash.mkdir()
    (trash / "managed.md").write_text(
        "---\ntype: knowledge-extract\narticle_id: hash1\nurl: https://example.com/a\n---\n正文",
        encoding="utf-8",
    )

    index = VaultIndex(tmp_path)
    index.rebuild()

    assert len(index.by_id["hash1"]) == 1


def test_by_url_lookup_requires_different_article_id(tmp_path):
    week = tmp_path / WEEK
    week.mkdir(parents=True)
    (week / "a.md").write_text(
        "---\ntype: knowledge-extract\narticle_id: oldhash\nurl: https://example.com/a\n---\n正文",
        encoding="utf-8",
    )
    index = VaultIndex(tmp_path)
    index.rebuild()

    same = index.lookup(content_hash="oldhash", url="https://example.com/a")
    assert same.by_hash is not None and same.by_url is None

    changed = index.lookup(content_hash="newhash", url="https://example.com/a")
    assert changed.by_url is not None and changed.by_url.article_id == "oldhash"
