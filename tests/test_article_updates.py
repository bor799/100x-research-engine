"""Same-URL increment detection, the 100x:updates managed block, and the
ArticleUpdater merge flow.

Production evidence behind the gate design: both real re-fetch mutations were
equal-length page-counter micro-changes (similarity 0.9985/0.9999), while a
real edit almost always shifts length. So the gate is ratio AND length-delta,
with the ratio computed on normalized text capped at 30k chars per side.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_extractor_v3.models import FetchedContent, TypedError
from knowledge_extractor_v3.outputs.updates import (
    UPDATES_END,
    UPDATES_START,
    append_update_entry,
    build_update_entry,
    extract_archived_text,
    looks_like_same_content,
    parse_increment_result,
    render_update_entry_md,
    sanitize_update_text,
    similarity_ratio,
)
from knowledge_extractor_v3.outputs.vault_index import VaultArticleRef
from knowledge_extractor_v3.queue_store import FailureKind, NextAction

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _fetched(text: str, *, url: str = "https://example.com/a", content_hash: str = "newhash0001") -> FetchedContent:
    return FetchedContent(
        url=url, source="fixture", source_type="web_article", title="标题",
        text=text, fetched_at="2026-08-30T00:00:00+00:00", content_hash=content_hash,
    )


def _no_update_json() -> str:
    return '```json\n{"has_update": false, "delta_summary": "", "new_points": [], "changed_facts": []}\n```'


def _update_json() -> str:
    return (
        "```json\n"
        '{"has_update": true, "delta_summary": "公司公布了新的 ARR 数据。", '
        '"new_points": ["ARR 从 200 万涨到 500 万"], "changed_facts": ["200 万 → 500 万"]}\n'
        "```"
    )


class RecordingLLM:
    def __init__(self, reply: str | TypedError) -> None:
        self.reply = reply
        self.calls = 0

    def __call__(self, content: str, prompt: str, *, stage: str = "increment") -> str | TypedError:
        self.calls += 1
        return self.reply


def _archived_article(path: Path) -> str:
    return (
        "---\ntype: knowledge-extract\narticle_id: oldhash\nurl: https://example.com/a\n"
        "title: 旧标题\n---\n\n# 旧标题\n\n- 判断一\n\n## 原文\n\n"
        "这是归档的完整原文内容，长度足以构成一段正文。\n"
    )


# --- 判重门 ---


def test_whitespace_and_case_noise_still_counts_as_same_content():
    old = "Body text with   mixed\n\n whitespace and CASE variations."
    new = "body text with mixed whitespace and case variations."
    assert similarity_ratio(old, new) >= 0.98
    assert looks_like_same_content(old, new)


def test_real_edit_falls_below_threshold():
    old = " ".join(f"段落{i}的内容保持一致。" for i in range(30))
    new = old.replace("段落15的内容保持一致。", "段落15的内容被完全改写，加入了新的结论与证据。")
    assert not looks_like_same_content(old, new)


def test_tail_update_on_long_article_is_caught_by_length_gate():
    """The two truncated texts still match >= 0.98 (the cap hides the tail),
    so only the untruncated length delta exposes the append."""
    base = "长文正文。" * 12_000  # ~60k chars, well past the 30k cap
    updated = base + "追加在尾部的三百字新增章节，包含全新事实。" * 10
    assert similarity_ratio(base, updated) >= 0.98
    assert not looks_like_same_content(base, updated)


# --- 解析与清洗 ---


def test_parse_increment_accepts_fenced_json():
    parsed = parse_increment_result(_update_json())
    assert not isinstance(parsed, TypedError)
    assert parsed["has_update"] is True
    assert parsed["changed_facts"] == ["200 万 → 500 万"]


def test_parse_increment_maps_garbage_to_parse_error():
    parsed = parse_increment_result("这不是 JSON")
    assert isinstance(parsed, TypedError)
    assert parsed.failure_kind is FailureKind.PARSE_ERROR
    assert parsed.next_action is NextAction.INVESTIGATE


def test_sanitize_strips_html_comments():
    dirty = "正文<!-- 100x:updates:end -->More --> 以及\n\n\n\n多余空行"
    assert sanitize_update_text(dirty) == "正文More --> 以及\n\n多余空行"


# --- 追加条目 ---


def test_append_entry_is_idempotent_and_leaves_other_blocks_alone(tmp_path):
    path = tmp_path / "article.md"
    path.write_text(
        "---\ntype: knowledge-extract\narticle_id: oldhash\n---\n\n正文\n\n"
        "<!-- 100x:user-feedback:start -->\n我的批注\n<!-- 100x:user-feedback:end -->\n",
        encoding="utf-8",
    )
    entry_md = render_update_entry_md({
        "date": "2026-08-30", "source": "fixture", "url": "https://example.com/a",
        "summary": "新增了 ARR 数据", "new_points": [], "changed_facts": [],
        "content_hash": "hash-a",
    })
    assert "hash-a" in entry_md  # renderer carries the idempotency marker
    assert append_update_entry(path, entry_md, content_hash="hash-a")
    body = path.read_text(encoding="utf-8")
    assert UPDATES_START in body and entry_md in body
    assert "我的批注" in body

    assert not append_update_entry(path, "### 重复 · 来源 fixture\n- 增量：重复", content_hash="hash-a")
    after = path.read_text(encoding="utf-8")
    assert after == body


def test_extract_archived_text_skips_updates_block():
    markdown = (
        "\n## 原文\n\n原始正文第一段。\n\n"
        f"{UPDATES_START}\n### 2026-08-29 · 来源 fixture\n- 增量：旧增量\n{UPDATES_END}\n"
    )
    assert "旧增量" not in extract_archived_text(markdown)
    assert "原始正文第一段。" in extract_archived_text(markdown)


# --- merge_update 三分支 ---


def _ref(path: Path) -> VaultArticleRef:
    return VaultArticleRef(
        article_id="oldhash", path=path, week=path.parent.name,
        url="https://example.com/a", title="旧标题",
    )


def test_merge_update_same_content_returns_duplicate_with_zero_llm_calls(tmp_path):
    from knowledge_extractor_v3.outputs.updates import ArticleUpdater

    path = tmp_path / "2026-08-W4" / "article.md"
    path.parent.mkdir(parents=True)
    archived = _archived_article(path)
    path.write_text(archived, encoding="utf-8")
    llm = RecordingLLM(_update_json())
    updater = ArticleUpdater(tmp_path, complete_fn=llm, project_root=PROJECT_ROOT)

    outcome = updater.merge_update(_ref(path), _fetched("这是归档的完整原文内容，长度足以构成一段正文。"))

    assert outcome.kind == "duplicate"
    assert llm.calls == 0
    assert path.read_text(encoding="utf-8") == archived


def test_merge_update_no_update_leaves_file_untouched(tmp_path):
    from knowledge_extractor_v3.outputs.updates import ArticleUpdater

    path = tmp_path / "2026-08-W4" / "article.md"
    path.parent.mkdir(parents=True)
    archived = _archived_article(path)
    path.write_text(archived, encoding="utf-8")
    llm = RecordingLLM(_no_update_json())
    updater = ArticleUpdater(tmp_path, complete_fn=llm, project_root=PROJECT_ROOT)

    outcome = updater.merge_update(_ref(path), _fetched("完全不同的新版本文本，长度也变了。" * 5))

    assert outcome.kind == "no_update"
    assert llm.calls == 1
    assert path.read_text(encoding="utf-8") == archived


def test_merge_update_appends_entry_and_flags_state(tmp_path):
    from knowledge_extractor_v3.magazine import MagazineStore
    from knowledge_extractor_v3.outputs.updates import ArticleUpdater

    path = tmp_path / "2026-08-W4" / "article.md"
    path.parent.mkdir(parents=True)
    path.write_text(_archived_article(path), encoding="utf-8")
    llm = RecordingLLM(_update_json())
    updater = ArticleUpdater(tmp_path, complete_fn=llm, project_root=PROJECT_ROOT)

    outcome = updater.merge_update(_ref(path), _fetched("内容大幅更新的新版本。" * 20, content_hash="newhash0002"))

    assert outcome.kind == "merged"
    body = path.read_text(encoding="utf-8")
    assert "ARR 从 200 万涨到 500 万" in body
    assert "## 原文" in body  # base extraction never rewritten
    state = MagazineStore(tmp_path).load_week("2026-08-W4")["articles"]["oldhash"]
    assert state["update_pending"] is True
    assert [u["content_hash"] for u in state["updates"]] == ["newhash0002"]


def test_merge_update_propagates_typed_error(tmp_path):
    from knowledge_extractor_v3.outputs.updates import ArticleUpdater

    path = tmp_path / "2026-08-W4" / "article.md"
    path.parent.mkdir(parents=True)
    path.write_text(_archived_article(path), encoding="utf-8")
    error = TypedError(
        failure_kind=FailureKind.LLM_RATE_LIMIT,
        message="provider throttled", stage="increment",
        retryable=True, next_action=NextAction.RETRY_LATER,
    )
    updater = ArticleUpdater(tmp_path, complete_fn=RecordingLLM(error), project_root=PROJECT_ROOT)

    result = updater.merge_update(_ref(path), _fetched("完全不同的新版本文本。" * 8))

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.LLM_RATE_LIMIT


# --- state 侧 ---


def test_record_update_is_idempotent_per_hash(tmp_path):
    from knowledge_extractor_v3.magazine import MagazineStore

    week = tmp_path / "2026-08-W4"
    week.mkdir(parents=True)
    (week / "article.md").write_text(_archived_article(week / "article.md"), encoding="utf-8")
    entry = {
        "date": "2026-08-30", "source": "fixture", "url": "https://example.com/a",
        "summary": "s", "new_points": [], "changed_facts": [], "content_hash": "h1",
    }
    store = MagazineStore(tmp_path)
    store.record_update("oldhash", entry)
    store.record_update("oldhash", dict(entry))
    state = store.load_week("2026-08-W4")["articles"]["oldhash"]

    assert len(state["updates"]) == 1
    assert state["update_pending"] is True


def test_rendered_entry_round_trips_through_build(tmp_path):
    fetched = _fetched("正文", content_hash="hash-x")
    parsed = parse_increment_result(_update_json())
    assert not isinstance(parsed, TypedError)
    entry = build_update_entry(fetched, parsed)
    md = render_update_entry_md(entry)
    assert entry["summary"] == "公司公布了新的 ARR 数据。"
    assert "200 万 → 500 万" in md
    # LLM text is comment-free; the only marker is the renderer's own hash line.
    assert md.count("<!--") == 1 and "100x:update:hash-x" in md
