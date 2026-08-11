from __future__ import annotations

import json

from knowledge_extractor_v3.models import FetchedContent, TypedError
from knowledge_extractor_v3.outputs.wechat_queue import WechatQueue
from knowledge_extractor_v3.queue_store import FailureKind


def _content(*, score: float = 8.2, metadata: dict[str, object] | None = None) -> FetchedContent:
    return FetchedContent(
        url="https://example.com/story",
        source="test",
        source_type="web_article",
        title="Three People Built a Business",
        text="body",
        fetched_at="2026-08-10T00:00:00+00:00",
        content_hash="abc123def456",
        metadata={"score": score, **(metadata or {})},
    )


def test_deliver_writes_expected_utf8_json_atomically(tmp_path):
    queue = WechatQueue(tmp_path / "wechat")

    status, preview = queue.deliver(_content(), "3个人，做到了200万ARR。")

    assert status == "queued"
    assert preview == "3个人，做到了200万ARR。"
    files = list((tmp_path / "wechat" / "pending").glob("*.json"))
    assert len(files) == 1
    assert not list((tmp_path / "wechat").glob(".tmp-*.json"))
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["text"] == "3个人，做到了200万ARR。"
    assert payload["url"] == "https://example.com/story"
    assert payload["lane"] == "business"
    assert payload["event_id"] == "abc123def456"
    assert payload["final_score"] == 0.0
    assert payload["business_story_fit"] == 0.0
    assert payload["prompt_hash"] == ""
    assert payload["attempts"] == 0
    assert payload["expires_at"].endswith("+00:00")
    assert payload["created_at"].endswith("+00:00")


def test_deliver_dedupes_by_event_id(tmp_path):
    """The outbox dedupes by event_id (= content hash). Delivering the same
    content twice keeps a single pending item."""
    queue = WechatQueue(tmp_path / "wechat")

    first = queue.deliver(_content(), "first")
    second = queue.deliver(_content(), "second")

    assert first[0] == "queued"
    assert second[0] == "duplicate"
    assert len(list((tmp_path / "wechat" / "pending").glob("*.json"))) == 1


def test_each_explicit_wechat_request_gets_one_reply_even_for_same_article(tmp_path):
    queue = WechatQueue(tmp_path / "wechat")
    first_request = _content(metadata={
        "reply_channel": "wechat",
        "reply_request_key": "7:2026-08-11T10:00:00+00:00",
    })
    repeated_same_request = _content(metadata={
        "reply_channel": "wechat",
        "reply_request_key": "7:2026-08-11T10:00:00+00:00",
    })
    new_request = _content(metadata={
        "reply_channel": "wechat",
        "reply_request_key": "7:2026-08-11T11:00:00+00:00",
    })

    assert queue.deliver(first_request, "first", lane="requested")[0] == "queued"
    assert queue.deliver(repeated_same_request, "retry", lane="requested")[0] == "duplicate"
    assert queue.deliver(new_request, "asked again", lane="requested")[0] == "queued"
    assert len(list((tmp_path / "wechat" / "pending").glob("*.json"))) == 2
    assert all(item.lane == "requested" for item in queue.outbox._list("pending"))


def test_deliver_returns_typed_error_when_queue_path_is_a_file(tmp_path):
    queue_path = tmp_path / "not-a-directory"
    queue_path.write_text("occupied", encoding="utf-8")

    result = WechatQueue(queue_path).deliver(_content(), "brief")

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.OUTPUT_FAILED
    assert result.stage == "output.wechat_queue"
    assert result.retryable is True
