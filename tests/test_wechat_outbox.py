"""Tests for the durable WeChat outbox state machine."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from knowledge_extractor_v3.outputs.wechat_outbox import (
    BUSINESS_LANE,
    STRATEGIC_LANE,
    OutboxItem,
    WechatOutbox,
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


def test_enqueue_writes_to_pending(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    assert box.enqueue(_item("e1"))
    assert (tmp_path / "ob" / "pending" / "e1.json").exists()


def test_enqueue_dedupes_by_event_id(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    assert box.enqueue(_item("e1"))
    assert box.enqueue(_item("e1")) is False  # duplicate
    assert len(list((tmp_path / "ob" / "pending").glob("*.json"))) == 1


def test_claim_moves_pending_to_processing(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("e1"))
    claimed = box.claim()
    assert len(claimed) == 1
    assert claimed[0].event_id == "e1"
    assert (tmp_path / "ob" / "processing" / "e1.json").exists()
    assert not (tmp_path / "ob" / "pending" / "e1.json").exists()


def test_claim_returns_empty_when_nothing_pending(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    assert box.claim() == []


def test_claim_filters_by_lane(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("b1", lane=BUSINESS_LANE))
    box.enqueue(_item("s1", lane=STRATEGIC_LANE))
    claimed = box.claim(lane=STRATEGIC_LANE)
    assert len(claimed) == 1
    assert claimed[0].event_id == "s1"


def test_claim_orders_business_by_bsf_then_final_score(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("low_bsf", business_story_fit=0.5, final_score=0.95))
    box.enqueue(_item("high_bsf", business_story_fit=0.9, final_score=0.8))
    claimed = box.claim(limit=2)
    # higher business_story_fit wins despite lower final_score
    assert claimed[0].event_id == "high_bsf"
    assert claimed[1].event_id == "low_bsf"


def test_ack_moves_processing_to_sent(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("e1"))
    box.claim()
    assert box.ack("e1") is True
    assert (tmp_path / "ob" / "sent" / "e1.json").exists()
    assert not (tmp_path / "ob" / "processing" / "e1.json").exists()


def test_ack_returns_false_for_unknown(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    assert box.ack("nope") is False


def test_nack_returns_to_pending_and_increments_attempts(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("e1"))
    box.claim()
    result = box.nack("e1")
    assert result == "pending"
    data = json.loads((tmp_path / "ob" / "pending" / "e1.json").read_text())
    assert data["attempts"] == 1


def test_nack_moves_to_failed_after_max_attempts(tmp_path):
    box = WechatOutbox(tmp_path / "ob", max_attempts=3)
    box.enqueue(_item("e1"))
    for _ in range(3):
        box.claim()
        box.nack("e1")
    assert (tmp_path / "ob" / "failed" / "e1.json").exists()
    assert not (tmp_path / "ob" / "pending" / "e1.json").exists()


def test_expire_removes_business_items_past_24h(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    box.enqueue(_item("old", created_at=old, expires_at=old))
    box.enqueue(_item("new"))
    expired = box.expire()
    assert expired == 1
    assert not (tmp_path / "ob" / "pending" / "old.json").exists()
    assert (tmp_path / "ob" / "pending" / "new.json").exists()


def test_expire_uses_longer_ttl_for_strategic_lane(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    two_days_ago = (datetime.now(UTC) - timedelta(hours=48)).isoformat()
    # Strategic items live 8 days; 2 days old should survive.
    box.enqueue(_item("s", lane=STRATEGIC_LANE, created_at=two_days_ago, expires_at=two_days_ago))
    assert box.expire() == 0
    assert (tmp_path / "ob" / "pending" / "s.json").exists()


def test_recover_stale_processing_returns_to_pending(tmp_path):
    box = WechatOutbox(tmp_path / "ob")
    box.enqueue(_item("e1"))
    box.claim()
    # Backdate the processing file's mtime so it looks stale.
    p = tmp_path / "ob" / "processing" / "e1.json"
    import os, time
    old = time.time() - 9999
    os.utime(p, (old, old))
    recovered = box.recover_stale_processing(stale_seconds=600)
    assert recovered == 1
    assert (tmp_path / "ob" / "pending" / "e1.json").exists()
