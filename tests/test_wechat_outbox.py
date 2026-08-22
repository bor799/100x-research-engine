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
        action_value=0.85,
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
    box.enqueue(_item("high_score", final_score=0.95))
    box.enqueue(_item("low_score", final_score=0.8))
    box.enqueue(_item("strategic", lane=STRATEGIC_LANE))
    claimed = box.claim(lane=BUSINESS_LANE, limit=2)
    # V4 orders by final_score (the headline rank), not action_value.
    assert [item.event_id for item in claimed] == ["high_score", "low_score"]


def test_claim_round_robins_by_source(tmp_path):
    """One prolific source must not fill the whole digest window."""
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("a1", source="Alpha", action_value=0.9, url="https://a/1"))
    box.enqueue(_item("a2", source="Alpha", action_value=0.85, url="https://a/2"))
    box.enqueue(_item("b1", source="Beta", action_value=0.8, url="https://b/1"))
    claimed = box.claim(lane=BUSINESS_LANE, limit=2)
    assert [item.event_id for item in claimed] == ["a1", "b1"]
    assert _read(root, "processing", "a1")["source"] == "Alpha"


def test_claim_backfills_when_fewer_sources_than_slots(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("a1", source="Alpha", action_value=0.9, url="https://a/1"))
    box.enqueue(_item("a2", source="Alpha", action_value=0.8, url="https://a/2"))
    claimed = box.claim(lane=BUSINESS_LANE, limit=2)
    assert [item.event_id for item in claimed] == ["a1", "a2"]


def test_claim_sourceless_items_still_fill_window(tmp_path):
    """Legacy/manual items without a source never collide with each other."""
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("legacy1", action_value=0.9, url="https://x/1"))
    box.enqueue(_item("legacy2", action_value=0.8, url="https://x/2"))
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


def test_recover_stale_processing_releases_without_burning_attempt(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    path = root / "processing" / "e1.json"
    old = time.time() - 9_999
    os.utime(path, (old, old))
    assert box.recover_stale_processing(stale_seconds=600) == 1
    payload = _read(root, "pending")
    assert payload["attempts"] == 0
    attempt = payload["delivery_attempts"][0]
    assert attempt["status"] == "released"
    assert attempt["receipt"]["error_code"] == "STALE_CLAIM_RECOVERED"


def test_recover_stale_processing_skips_unreadable_file(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    path = root / "processing" / "e1.json"
    old = time.time() - 9_999
    os.utime(path, (old, old))
    path.write_text("{not json", encoding="utf-8")
    assert box.recover_stale_processing(stale_seconds=600) == 0
    assert path.exists()


def test_release_returns_to_pending_without_consuming_attempt(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    assert box.release("e1", _receipt(ok=False)) == "pending"
    payload = _read(root, "pending")
    assert payload["attempts"] == 0
    attempt = payload["delivery_attempts"][0]
    assert attempt["status"] == "released"
    assert attempt["receipt"]["error_code"] == "SEND_FAILED"
    assert payload["claimed_at"]  # retained for the timeline, like nack


def test_release_missing_unknown_and_after_ack_return_missing(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    assert box.release("ghost", _receipt(ok=False)) == "missing"
    box.enqueue(_item("e1"))
    box.claim()
    box.ack("e1", _receipt())
    assert box.release("e1", _receipt(ok=False)) == "missing"
    box.enqueue(_item("e2"))
    box.claim()
    assert box.release("e2", _receipt(ok=False)) == "pending"
    assert box.release("e2", _receipt(ok=False)) == "missing"  # double release


def test_release_then_reclaim_reuses_attempt_number(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    box.release("e1", _receipt(ok=False))
    claimed = box.claim()
    assert claimed[0].attempts == 1
    payload = _read(root, "processing")
    assert [attempt["attempt"] for attempt in payload["delivery_attempts"]] == [1, 1]
    assert payload["delivery_attempts"][0]["status"] == "released"
    assert payload["delivery_attempts"][1]["status"] == "processing"


def test_release_sanitizes_receipt(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1"))
    box.claim()
    box.release("e1", _receipt(ok=False))
    receipt = _read(root, "pending")["delivery_attempts"][0]["receipt"]
    assert receipt["raw_response"]["token"] == "[REDACTED]"
    assert receipt["recipient_ref"].startswith("sha256:")


def _three_strike_failure(box, event_id="e1", error_code="SEND_FAILED"):
    box.enqueue(_item(event_id))
    for _ in range(3):
        box.claim()
        receipt = _receipt(ok=False)
        receipt["error_code"] = error_code
        box.nack(event_id, receipt)


def test_requeue_failed_resets_attempts_and_appends_marker(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    box.enqueue(_item("e1", expires_at="2026-08-21T00:00:00+00:00"))
    for _ in range(3):
        box.claim()
        box.nack("e1", _receipt(ok=False))
    before = _read(root, "failed")
    summary = box.requeue_failed(reason="iLink outage; WeChat re-login completed")
    assert summary.requeued == ("e1",)
    assert summary.skipped == ()
    payload = _read(root, "pending")
    assert payload["attempts"] == 0
    assert payload["state"] == "pending"
    assert len(payload["delivery_attempts"]) == 3  # ledger preserved
    assert payload["requeues"] == [{
        "reason": "iLink outage; WeChat re-login completed",
        "requeued_at": payload["requeues"][0]["requeued_at"],
        "prior_failed_at": before["failed_at"],
    }]
    assert payload["expires_at"] > before["expires_at"]  # fresh TTL window


def test_requeue_failed_skips_quality_gate_blocked_by_default(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    _three_strike_failure(box, "blocked", error_code="QUALITY_GATE_BLOCKED")
    summary = box.requeue_failed(reason="post-outage catch-up")
    assert summary.requeued == ()
    assert summary.skipped == (("blocked", "content_blocked"),)
    assert _read(root, "failed", "blocked")["state"] == "failed"


def test_requeue_failed_event_id_override_requeues_content_blocked(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    _three_strike_failure(box, "blocked", error_code="QUALITY_GATE_BLOCKED")
    summary = box.requeue_failed(reason="operator override", event_ids=("blocked",))
    assert summary.requeued == ("blocked",)
    assert _read(root, "pending", "blocked")["attempts"] == 0


def test_requeue_failed_lane_filter(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    _three_strike_failure(box, "biz")
    box.enqueue(_item("strat", lane=STRATEGIC_LANE))
    for _ in range(3):
        box.claim(lane=STRATEGIC_LANE)
        box.nack("strat", _receipt(ok=False))
    summary = box.requeue_failed(reason="post-outage", lane=STRATEGIC_LANE)
    assert summary.requeued == ("strat",)
    assert ("biz", "lane_mismatch") in summary.skipped
    assert (root / "failed" / "biz.json").exists()


def test_requeue_failed_refreshes_expired_ttl_and_can_keep_it(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    stale = (datetime.now(UTC) - timedelta(hours=48)).replace(microsecond=0).isoformat()
    box.enqueue(_item("e1", created_at=stale, expires_at=stale))
    for _ in range(3):
        box.claim()
        box.nack("e1", _receipt(ok=False))
    kept = box.requeue_failed(reason="audit requeue", refresh_ttl=False)
    assert kept.requeued == ("e1",)
    assert _read(root, "pending")["expires_at"] == stale
    # Fail it again to verify the default TTL refresh.
    for _ in range(3):
        box.claim()
        box.nack("e1", _receipt(ok=False))
    summary = box.requeue_failed(reason="real requeue")
    assert summary.requeued == ("e1",)
    assert _read(root, "pending")["expires_at"] > stale


def test_requeue_failed_preserves_idempotency_marker_and_blocks_replay(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    _three_strike_failure(box)
    markers_before = sorted(p.name for p in (root / "idempotency").glob("*.json"))
    box.requeue_failed(reason="post-outage")
    markers_after = sorted(p.name for p in (root / "idempotency").glob("*.json"))
    assert markers_before == markers_after
    assert box.enqueue(_item("e1")) is False


def test_requeue_failed_skips_when_already_pending(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    _three_strike_failure(box, "dead")
    # Hand-place a same-id pending item to simulate a concurrent requeue race.
    dead = _read(root, "failed", "dead")
    (root / "pending").mkdir(parents=True, exist_ok=True)
    (root / "pending" / "dead.json").write_text(json.dumps(dead), encoding="utf-8")
    summary = box.requeue_failed(reason="race check")
    assert summary.requeued == ()
    assert ("dead", "already_pending") in summary.skipped


def test_requeue_failed_skips_unreadable_and_reports_unknown_ids(tmp_path):
    root = tmp_path / "ob"
    box = WechatOutbox(root)
    (root / "failed").mkdir(parents=True, exist_ok=True)
    (root / "failed" / "junk.json").write_text("{not json", encoding="utf-8")
    summary = box.requeue_failed(reason="sweep", event_ids=("ghost",))
    assert summary.requeued == ()
    assert ("junk", "unreadable") in summary.skipped
    assert ("ghost", "not_in_failed") in summary.skipped


def test_requeue_failed_requires_reason(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    with pytest.raises(ValueError):
        box.requeue_failed(reason="  ")


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
