"""CLI-level tests for the Cindy WeChat outbox consumer script."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from knowledge_extractor_v3.outputs.wechat_outbox import OutboxItem, WechatOutbox, ttl_for_lane


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str):
    return subprocess.run(
        [sys.executable, "scripts/wechat_outbox.py", *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _item(event_id="e1"):
    return OutboxItem(
        event_id=event_id,
        lane="business",
        text="brief",
        url="https://example.com/a",
        final_score=0.8,
        action_value=0.85,
        prompt_hash="h",
        created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        expires_at=ttl_for_lane("business"),
        attempts=0,
    )


def _receipt_args(*, ok: bool) -> list[str]:
    args = [
        "--agent-kind", "claude-code",
        "--tool", "cindy_wechat.send_message_to_user",
        "--started-at", "2026-08-22T09:20:12+00:00",
        "--finished-at", "2026-08-22T09:20:40+00:00",
    ]
    if ok:
        return [*args, "--message-id", "message-1"]
    return [*args, "--error-code", "SEND_FAILED", "--error-message", "channel down"]


def _three_strike_failure(box: WechatOutbox, event_id: str, error_code: str = "SEND_FAILED") -> None:
    box.enqueue(_item(event_id))
    for _ in range(3):
        box.claim()
        receipt = {
            "agent_context": {"agent_kind": "claude-code"},
            "tool": "cindy_wechat.send_message_to_user",
            "started_at": "2026-08-22T09:20:12+00:00",
            "finished_at": "2026-08-22T09:20:40+00:00",
            "message_id": "",
            "error_code": error_code,
            "error_message": "boom",
            "raw_response": "",
        }
        box.nack(event_id, receipt)


def test_cli_release_roundtrip_without_consuming_attempt(tmp_path):
    queue_dir = tmp_path / "ob"
    box = WechatOutbox(queue_dir)
    box.enqueue(_item())
    box.claim()

    result = _run(
        "--queue-dir", str(queue_dir),
        "release", "e1",
        *_receipt_args(ok=False),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "pending"
    counts = json.loads(_run("--queue-dir", str(queue_dir), "status").stdout)
    assert counts == {"failed": 0, "pending": 1, "processing": 0, "sent": 0}


def test_cli_release_missing_exits_1(tmp_path):
    result = _run(
        "--queue-dir", str(tmp_path / "ob"),
        "release", "ghost",
        *_receipt_args(ok=False),
    )
    assert result.returncode == 1
    assert "not found in processing" in result.stderr


def test_cli_requeue_failed_reports_json_and_exit_codes(tmp_path):
    queue_dir = tmp_path / "ob"
    box = WechatOutbox(queue_dir)
    _three_strike_failure(box, "channel_dead")
    _three_strike_failure(box, "content_blocked", error_code="QUALITY_GATE_BLOCKED")

    result = _run(
        "--queue-dir", str(queue_dir),
        "requeue-failed", "--reason", "iLink outage; re-login completed",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["requeued"] == ["channel_dead"]
    assert payload["skipped"] == [{"event_id": "content_blocked", "cause": "content_blocked"}]
    counts = json.loads(_run("--queue-dir", str(queue_dir), "status").stdout)
    assert counts == {"failed": 1, "pending": 1, "processing": 0, "sent": 0}

    empty = _run(
        "--queue-dir", str(queue_dir),
        "requeue-failed", "--reason", "nothing left",
    )
    assert empty.returncode == 2
    assert json.loads(empty.stdout)["requeued"] == []


def test_cli_requeue_failed_requires_reason(tmp_path):
    result = _run("--queue-dir", str(tmp_path / "ob"), "requeue-failed")
    assert result.returncode != 0  # argparse rejects the missing required flag
