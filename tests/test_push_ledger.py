"""Tests for the WeChat push ledger (local vault landing)."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from knowledge_extractor_v3.outputs.push_ledger import PushLedger, week_label
from knowledge_extractor_v3.outputs.wechat_outbox import OutboxItem, ttl_for_lane

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _item(event_id="abc123", *, sent_at="2026-08-25T09:30:00+00:00", lane="business"):
    return OutboxItem(
        event_id=event_id,
        lane=lane,
        text="🎯 Headlong：agent 永不休眠\n\n💡 一行摘要。\n\n📊 评分: 8.2 · Tier A",
        url="https://example.com/a",
        final_score=0.8225,
        action_value=0.7,
        prompt_hash="h",
        created_at="2026-08-25T05:27:19+00:00",
        expires_at=ttl_for_lane(lane),
        attempts=1,
        sent_at=sent_at,
        source="agent-reach-web",
    )


def test_week_label_matches_vault_convention():
    assert week_label(datetime(2026, 8, 25).date()) == "2026-08-W4"
    assert week_label(datetime(2026, 8, 17).date()) == "2026-08-W3"
    assert week_label(datetime(2026, 8, 1).date()) == "2026-08-W1"
    assert week_label(datetime(2026, 8, 31).date()) == "2026-08-W5"


def test_land_creates_week_file_with_header_and_entry(tmp_path):
    ledger = PushLedger(tmp_path)
    outcome = ledger.land(_item())
    assert outcome.status == "landed"
    path = tmp_path / "2026-08-W4" / "微信推送 2026-08-W4.md"
    assert outcome.path == path
    body = path.read_text(encoding="utf-8")
    assert body.startswith("---\n")
    assert "title: 微信推送周记录（2026-08-W4）" in body
    assert "## 09:30 🎯 Headlong：agent 永不休眠" in body
    assert "车道: 商业故事" in body
    assert "final_score: 0.8225" in body
    assert "https://example.com/a" in body
    assert "(event:abc123)" in body
    assert "> 🎯 Headlong" in body  # card text quoted verbatim


def test_land_links_full_extraction_note(tmp_path):
    notes = tmp_path / "AI进展"
    notes.mkdir()
    note = notes / "2026-08-25-headlong-agent-abc123.md"
    note.write_text("---\ntitle: x\n---\nbody", encoding="utf-8")
    ledger = PushLedger(tmp_path)
    ledger.land(_item(event_id="abc123"))
    body = (tmp_path / "2026-08-W4" / "微信推送 2026-08-W4.md").read_text(encoding="utf-8")
    assert "[[AI进展/2026-08-25-headlong-agent-abc123]]" in body


def test_land_prefers_week_article_link(tmp_path):
    week = tmp_path / "2026-08-W4"
    week.mkdir()
    note = week / "2026-08-25 Headlong abc123fu.md"
    note.write_text("---\ntype: knowledge-extract\n---\nbody", encoding="utf-8")
    PushLedger(tmp_path).land(_item(event_id="abc123full"))
    body = (week / "微信推送 2026-08-W4.md").read_text(encoding="utf-8")
    assert "[[2026-08-W4/2026-08-25 Headlong abc123fu]]" in body


def test_land_is_idempotent_by_event_id(tmp_path):
    ledger = PushLedger(tmp_path)
    assert ledger.land(_item()).status == "landed"
    second = ledger.land(_item())
    assert second.status == "duplicate"
    body = (tmp_path / "2026-08-W4" / "微信推送 2026-08-W4.md").read_text(encoding="utf-8")
    assert body.count("(event:abc123)") == 1


def test_land_separates_weeks_by_send_date(tmp_path):
    ledger = PushLedger(tmp_path)
    ledger.land(_item(event_id="w3", sent_at="2026-08-17T10:00:00+00:00"))
    ledger.land(_item(event_id="w4", sent_at="2026-08-25T09:30:00+00:00"))
    assert (tmp_path / "2026-08-W3" / "微信推送 2026-08-W3.md").exists()
    assert (tmp_path / "2026-08-W4" / "微信推送 2026-08-W4.md").exists()


def test_land_returns_error_without_raising(tmp_path, monkeypatch):
    ledger = PushLedger(tmp_path)
    # A file blocking the ledger directory name makes appends fail.
    blocker = tmp_path / "2026-08-W4"
    blocker.write_text("not a directory", encoding="utf-8")
    outcome = ledger.land(_item())
    assert outcome.status == "error"
    assert outcome.detail


def _run(*args: str, env_extra: dict[str, str] | None = None):
    import os

    env = {**os.environ, **(env_extra or {})}
    return subprocess.run(
        [sys.executable, "scripts/wechat_outbox.py", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _receipt_args() -> list[str]:
    return [
        "--agent-kind", "claude-code",
        "--tool", "cindy_wechat.send_message_to_user",
        "--started-at", "2026-08-25T09:20:12+00:00",
        "--finished-at", "2026-08-25T09:20:40+00:00",
        "--message-id", "message-1",
    ]


def test_cli_ack_lands_card_in_ledger(tmp_path):
    queue_dir = tmp_path / "ob"
    ledger_dir = tmp_path / "vault"

    from knowledge_extractor_v3.outputs.wechat_outbox import WechatOutbox

    box = WechatOutbox(queue_dir)
    box.enqueue(_item(event_id="ack1"))
    box.claim()

    result = _run(
        "--queue-dir", str(queue_dir),
        "ack", "ack1",
        *_receipt_args(),
        env_extra={"PUSH_LEDGER_DIR": str(ledger_dir)},
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["landed"] is True
    week_files = sorted(ledger_dir.glob("*/微信推送 *.md"))
    assert len(week_files) == 1  # ack stamps sent_at with the real clock
    assert "(event:ack1)" in week_files[0].read_text(encoding="utf-8")

    # Re-running land is a no-op (duplicate), keeping the ledger stable.
    backfill = _run(
        "--queue-dir", str(queue_dir),
        "land",
        env_extra={"PUSH_LEDGER_DIR": str(ledger_dir)},
    )
    assert backfill.returncode == 0, backfill.stderr
    summary = json.loads(backfill.stdout)
    assert summary["landed"] == 0
    assert summary["duplicates"] == 1


def test_cli_land_empty_sent_exits_2(tmp_path):
    result = _run(
        "--queue-dir", str(tmp_path / "ob"),
        "land",
        env_extra={"PUSH_LEDGER_DIR": str(tmp_path / "vault")},
    )
    assert result.returncode == 2


def test_cli_ack_with_sandbox_queue_never_touches_real_vault(tmp_path):
    """A --queue-dir override selects a sandbox outbox; without an explicit
    ledger dir the ack must not resolve the real config's vault root."""
    import os

    queue_dir = tmp_path / "ob"
    from knowledge_extractor_v3.outputs.wechat_outbox import WechatOutbox

    box = WechatOutbox(queue_dir)
    box.enqueue(_item(event_id="sandbox1"))
    box.claim()

    env = {k: v for k, v in os.environ.items() if k != "PUSH_LEDGER_DIR"}
    result = subprocess.run(
        [sys.executable, "scripts/wechat_outbox.py",
         "--queue-dir", str(queue_dir),
         "ack", "sandbox1",
         *_receipt_args()],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    # No ledger JSON was printed and nothing was created anywhere near cwd.
    assert '"landed"' not in result.stdout
    assert "ledger landing failed" not in result.stderr
