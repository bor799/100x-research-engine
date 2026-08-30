from __future__ import annotations

import time
import json
import urllib.request
from pathlib import Path

from knowledge_extractor_v3.magazine import (
    MagazineStore,
    MagazineServer,
    ReviewQueue,
    build_issue,
    issue_payload,
)
from knowledge_extractor_v3.models import ExtractionResult, FetchedContent, ScoreResult
from knowledge_extractor_v3.outputs.live_obsidian import LiveObsidianWriter


def _content(article_id: str = "abc123def456") -> FetchedContent:
    return FetchedContent(
        url="https://example.com/article",
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


def _extraction() -> ExtractionResult:
    return ExtractionResult(
        prompt_bundle="p", prompt_hash="h", model_route="stub", raw_text="{}", parsed={},
        title="压缩后的中文标题", one_line_signal="一句话",
        obsidian_brief_markdown="# 压缩后的中文标题\n\n- 判断一\n- 判断二",
    )


def _write(tmp_path: Path, monkeypatch, article_id: str = "abc123def456") -> Path:
    monkeypatch.setattr(
        "knowledge_extractor_v3.outputs.live_obsidian.utc_now",
        lambda: "2026-08-28T00:30:00+00:00",
    )
    result = LiveObsidianWriter(tmp_path, subdir="AI进展", write_manifest=False).write(
        _content(article_id), _score(), _extraction(), prompt_bundle="p", prompt_hash="h"
    )
    return Path(result)


def test_article_is_week_bucketed_with_summary_before_original(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch)
    assert path.parent == tmp_path / "2026-08-W4"
    assert "压缩后的中文标题" in path.name
    body = path.read_text(encoding="utf-8")
    assert body.index("- 判断一") < body.index("## 原文") < body.index("这是抓取后的完整原文")
    assert not (tmp_path / "AI进展").exists()


def test_issue_persists_read_comment_and_annotation_into_markdown(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch)
    store = MagazineStore(tmp_path)
    store.update("abc123def456", {"read": True, "comment": "这是我的判断。"})
    store.add_annotation("abc123def456", {"quote": "判断一", "comment": "需要验证"})

    payload = issue_payload(tmp_path, "2026-08-W4")
    article = payload["articles"][0]
    assert article["complete"] is True
    assert article["state"]["disposition"] == "commented"
    body = path.read_text(encoding="utf-8")
    assert "这是我的判断。" in body
    assert "> 判断一" in body
    assert "批注：需要验证" in body


def test_weekly_html_is_portable_but_uses_local_api_for_edits(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    issue = build_issue(tmp_path, "2026-08-W4")
    body = issue.read_text(encoding="utf-8")
    assert issue.name == "知识萃取周刊 2026-08-W4.html"
    assert "保存当前划线" in body
    assert "交给 AI" in body
    assert "回到 Obsidian 原文" in body
    assert "127.0.0.1:8765" in body
    assert "localStorage" not in body


def test_archive_band_stays_on_disk_but_out_of_the_issue(tmp_path, monkeypatch):
    """Operator decision 2026-08-30: only push-band content (>= 0.75) enters
    the magazine. A 6.0-7.4 archive-band extraction is still written to the
    week folder, but the issue and reading state never track it."""
    _write(tmp_path, monkeypatch)  # abc123def456, final_score 0.8 (push band)
    mid = tmp_path / "2026-08-W4" / "2026-08-28 中段内容 fed654cba321.md"
    mid.write_text(
        "---\n"
        'type: "knowledge-extract"\n'
        'article_id: "fed654cba321"\n'
        "title: 中段内容\n"
        "url: https://example.com/mid\n"
        "final_score: 0.68\n"
        'signal_tier: "B"\n'
        "---\n\n压缩萃取正文。\n\n## 原文\n\n原文正文。\n",
        encoding="utf-8",
    )
    payload = issue_payload(tmp_path, "2026-08-W4")
    ids = [a["article_id"] for a in payload["articles"]]
    assert ids == ["abc123def456"]  # 0.68 never shows up
    state = json.loads((tmp_path / "2026-08-W4" / "阅读状态 2026-08-W4.json").read_text("utf-8"))
    assert "fed654cba321" not in state["articles"]
    assert mid.exists()  # …but the cold archive file is intact on disk


def test_unfinished_article_rolls_into_later_issue(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch)
    later = issue_payload(tmp_path, "2026-09-W1")
    assert later["articles"][0]["carryover"] is True
    store = MagazineStore(tmp_path)
    store.update("abc123def456", {"read": True, "disposition": "no_comment"})
    assert issue_payload(tmp_path, "2026-09-W1")["articles"] == []
    assert path.exists()


def test_explicit_review_submit_writes_managed_ai_block(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch)
    store = MagazineStore(tmp_path)
    queue = ReviewQueue(store, lambda article, state: "### 复核结论\n\n事实与判断已分开。")
    state = queue.submit("abc123def456")
    assert state["review"]["status"] in {"queued", "running", "done"}
    for _ in range(100):
        _, current, _ = store.find("abc123def456")
        if current["review"]["status"] == "done":
            break
        time.sleep(.01)
    assert current["review"]["status"] == "done"
    assert "事实与判断已分开" in path.read_text(encoding="utf-8")


def test_loopback_api_persists_article_state(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    server = MagazineServer(tmp_path, port=0)
    server.start()
    port = server.server.server_address[1]
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/articles/abc123def456",
            data=json.dumps({"read": True, "disposition": "no_comment"}).encode(),
            method="PATCH",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            state = json.load(response)
        assert state["read_at"]
        assert state["disposition"] == "no_comment"
    finally:
        server.close()


def test_repeat_write_preserves_user_feedback(tmp_path, monkeypatch):
    path = _write(tmp_path, monkeypatch)
    MagazineStore(tmp_path).update("abc123def456", {"read": True, "comment": "不要覆盖我"})
    _write(tmp_path, monkeypatch)
    assert "不要覆盖我" in path.read_text(encoding="utf-8")


def test_pending_update_reshelves_completed_article(tmp_path, monkeypatch):
    """A completed old-week article re-enters later issues when a same-URL
    update lands, shows its latest delta, and leaves once disposed again."""
    _write(tmp_path, monkeypatch)
    store = MagazineStore(tmp_path)
    store.update("abc123def456", {"read": True, "disposition": "no_comment"})
    assert issue_payload(tmp_path, "2026-09-W1")["articles"] == []  # complete and shelved away

    store.record_update(
        "abc123def456",
        {"content_hash": "newhash1", "summary": "B 轮融资 2 亿美元落地", "date": "2026-09-01"},
    )
    later = issue_payload(tmp_path, "2026-09-W1")
    assert len(later["articles"]) == 1
    article = later["articles"][0]
    assert article["carryover"] is True
    assert article["update_pending"] is True
    assert article["latest_update"]["summary"] == "B 轮融资 2 亿美元落地"
    assert later["counts"]["updates_pending"] == 1
    # unfinished/unread semantics are untouched: the article stays "complete"
    # so the 17:20 digest consumer is unaffected by pending updates.
    assert later["counts"]["unfinished"] == 0
    assert later["counts"]["unread"] == 0

    # Disposing of the merged content clears the flag and shelves it again.
    store.update("abc123def456", {"disposition": "no_comment"})
    final = issue_payload(tmp_path, "2026-09-W1")
    assert final["articles"] == []
    assert final["counts"]["updates_pending"] == 0


def test_issue_html_renders_update_badge_and_backlog(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch)
    store = MagazineStore(tmp_path)
    store.update("abc123def456", {"read": True, "disposition": "no_comment"})
    store.record_update(
        "abc123def456",
        {"content_hash": "newhash2", "summary": "裁员 30%", "date": "2026-09-02"},
    )
    issue = build_issue(tmp_path, "2026-09-W1")
    body = issue.read_text(encoding="utf-8")
    assert "有更新" in body  # card meta badge + update-note template
    assert "update_pending" in body  # the JS consumes the flag for the shelf
    assert "未完阅读架" in body  # backlog shelf keeps completed-but-updated items
