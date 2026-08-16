"""Tests for the durable Cindy/WeChat delivery state machine."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta

import pytest

from knowledge_extractor_v3.outputs.wechat_outbox import (
    BUSINESS_LANE,
    STRATEGIC_LANE,
    OutboxItem,
    WechatOutbox,
    sanitize_receipt,
    ttl_for_lane,
)


def _item(event_id="e1", lane=BUSINESS_LANE, **kw):
    base = dict(
        event_id=event_id,
        lane=lane,
        text="brief",
        url="https://example.com/a",
        final_score=0.8,
        business_story_fit=0.85,
        prompt_hash="h",
        created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        expires_at=ttl_for_lane(lane),
        attempts=0,
    )
    base.update(kw)
    return OutboxItem(**base)


def _receipt(*, ok=True):
    return {
        "agent_context": {
            "agent_kind": "claude-code",
            "model": "glm-5.2",
            "task_id": "task-1",
            "run_id": "run-1",
        },
        "tool": "cindy_wechat.send_message_to_user",
        "recipient_ref": "raw-peer-value",
        "session_ref": "raw-session-value",
        "started_at": "2026-08-11T14:00:00+00:00",
        "finished_at": "2026-08-11T14:00:01+00:00",
        "message_id": "message-1" if ok else "",
        "error_code": "" if ok else "SEND_FAILED",
        "error_message": "" if ok else "iLink rejected message",
        "raw_response": {"ok": ok, "token": "must-not-leak", "peer_id": "raw-peer-value"},
    }


def _read(root, state, event_id="e1"):
    return json.loads((root / state / f"{event_id}.json").read_text(encoding="utf-8"))


def test_enqueue_writes_pending_and_permanent_idempotency_marker(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    assert box.enqueue(_item("e1"))
    payload = _read(root, "pending")
    assert payload["state"] == "pending"
    assert payload["enqueued_at"]
    assert payload["updated_at"]
    markers = list((root / "idempotency").glob("*.json"))
    assert len(markers) == 1
    assert "brief" not in markers[0].read_text(encoding="utf-8")


@pytest.mark.parametrize("state", ["pending", "processing", "sent", "failed"])
def test_enqueue_dedupes_event_id_across_all_states(tmp_path, state):
    box = WechatOutbox(tmp_path / "ob", max_attempts=1)
    assert box.enqueue(_item("e1"))
    if state != "pending":
        box.claim()
    if state == "sent":
        assert box.ack("e1", _receipt())
    elif state == "failed":
        assert box.nack("e1", _receipt(ok=False)) == "failed"
    assert box.find_state("e1") == state
    assert box.enqueue(_item("e1", text="replay")) is False


def test_claim_moves_to_processing_and_starts_attempt(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    claimed = box.claim()
    assert len(claimed) == 1
    assert claimed[0].attempts == 1
    assert claimed[0].state == "processing"
    payload = _read(root, "processing")
    assert payload["claimed_at"]
    assert payload["delivery_attempts"][0]["status"] == "processing"


def test_claim_filters_and_orders_by_lane_and_scores(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("low_bsf", business_story_fit=0.5, final_score=0.95))
    box.enqueue(_item("high_bsf", business_story_fit=0.9, final_score=0.8))
    box.enqueue(_item("strategic", lane=STRATEGIC_LANE))
    claimed = box.claim(lane=BUSINESS_LANE, limit=2)
    assert [item.event_id for item in claimed] == ["high_bsf", "low_bsf"]


def test_claim_round_robins_by_source(tmp_path):
    """One prolific source must not fill the whole digest window."""
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("a1", source="Alpha", business_story_fit=0.9, url="https://a/1"))
    box.enqueue(_item("a2", source="Alpha", business_story_fit=0.85, url="https://a/2"))
    box.enqueue(_item("b1", source="Beta", business_story_fit=0.8, url="https://b/1"))
    claimed = box.claim(lane=BUSINESS_LANE, limit=2)
    assert [item.event_id for item in claimed] == ["a1", "b1"]
    assert _read(root, "processing", "a1")["source"] == "Alpha"


def test_claim_backfills_when_fewer_sources_than_slots(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("a1", source="Alpha", business_story_fit=0.9, url="https://a/1"))
    box.enqueue(_item("a2", source="Alpha", business_story_fit=0.8, url="https://a/2"))
    claimed = box.claim(lane=BUSINESS_LANE, limit=2)
    assert [item.event_id for item in claimed] == ["a1", "a2"]


def test_claim_sourceless_items_still_fill_window(tmp_path):
    """Legacy/manual items without a source never collide with each other."""
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("legacy1", business_story_fit=0.9, url="https://x/1"))
    box.enqueue(_item("legacy2", business_story_fit=0.8, url="https://x/2"))
    claimed = box.claim(lane=BUSINESS_LANE, limit=2)
    assert [item.event_id for item in claimed] == ["legacy1", "legacy2"]


def test_ack_persists_complete_sanitized_receipt_and_timeline(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    assert box.ack("e1", _receipt()) is True
    payload = _read(root, "sent")
    attempt = payload["delivery_attempts"][0]
    receipt = attempt["receipt"]
    assert payload["state"] == "sent"
    assert payload["sent_at"]
    assert attempt["status"] == "sent"
    assert receipt["message_id"] == "message-1"
    assert receipt["recipient_ref"].startswith("sha256:")
    assert receipt["session_ref"].startswith("sha256:")
    assert receipt["raw_response"]["token"] == "[REDACTED]"
    assert receipt["raw_response"]["peer_id"].startswith("sha256:")


def test_nack_retries_twice_then_fails_and_keeps_all_receipts(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root, max_attempts=3)
    box.enqueue(_item("e1"))
    for expected_attempt in (1, 2, 3):
        claimed = box.claim()
        assert claimed[0].attempts == expected_attempt
        result = box.nack("e1", _receipt(ok=False))
        assert result == ("failed" if expected_attempt == 3 else "pending")
    payload = _read(root, "failed")
    assert payload["attempts"] == 3
    assert payload["failed_at"]
    assert [attempt["status"] for attempt in payload["delivery_attempts"]] == [
        "retryable_failure", "retryable_failure", "failed"
    ]
    assert all(attempt["receipt"]["error_code"] == "SEND_FAILED" for attempt in payload["delivery_attempts"])


def test_expire_moves_old_pending_to_failed_without_losing_ledger(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    old = (datetime.now(UTC) - timedelta(hours=25)).replace(microsecond=0).isoformat()
    box.enqueue(_item("old", created_at=old, expires_at=old))
    assert box.expire() == 1
    payload = _read(root, "failed", "old")
    assert payload["failure"]["code"] == "OUTBOX_EXPIRED"
    assert box.enqueue(_item("old")) is False


def test_strategic_item_uses_longer_ttl(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    two_days_ago = (datetime.now(UTC) - timedelta(hours=48)).replace(microsecond=0)
    box.enqueue(_item(
        "s",
        lane=STRATEGIC_LANE,
        created_at=two_days_ago.isoformat(),
        expires_at=(two_days_ago + timedelta(days=8)).isoformat(),
    ))
    assert box.expire() == 0
    assert (root / "pending" / "s.json").exists()


def test_recover_stale_processing_nacks_and_records_reason(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    path = root / "processing" / "e1.json"
    old = time.time() - 9_999
    os.utime(path, (old, old))
    assert box.recover_stale_processing(stale_seconds=600) == 1
    payload = _read(root, "pending")
    assert payload["attempts"] == 1
    receipt = payload["delivery_attempts"][0]["receipt"]
    assert receipt["error_code"] == "STALE_CLAIM_RECOVERED"


def test_reap_sent_keeps_replay_blocked_by_idempotency_marker(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    box.ack("e1", _receipt())
    payload = _read(root, "sent")
    payload["sent_at"] = "2020-01-01T00:00:00+00:00"
    (root / "sent" / "e1.json").write_text(json.dumps(payload), encoding="utf-8")
    assert box.reap_sent(retention_days=14) == 1
    assert box.find_state("e1") == "reaped"
    assert box.enqueue(_item("e1")) is False


def test_receipt_reports_missing_connector_observability_without_guessing():
    clean = sanitize_receipt({"tool": "cindy_wechat", "raw_response": {"ok": False}})
    assert clean["recipient_ref"] == ""
    assert clean["session_ref"] == ""
    assert clean["observability_gaps"] == ["recipient_ref", "session_ref"]
