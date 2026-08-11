from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from knowledge_extractor_v3.outputs.wechat_outbox import OutboxItem, WechatOutbox, ttl_for_lane
from knowledge_extractor_v3.queue_store import QueueStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(script: str, *args: str):
    return subprocess.run(
        [sys.executable, script, *args],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cindy_control_enqueues_url_and_reports_status(tmp_path):
    queue_db = tmp_path / ".100x_v3" / "queue.db"
    outbox = tmp_path / "outbox"
    result = _run(
        "scripts/cindy_control.py",
        "--queue-db", str(queue_db),
        "--outbox-dir", str(outbox),
        "enqueue-url", "https://example.com/article",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "pending"
    assert payload["interaction"] == "asynchronous"
    assert "固定模板" in payload["user_message"]
    assert QueueStore(queue_db).get_task(payload["task_id"]).reply_channel == "wechat"

    status = _run(
        "scripts/cindy_control.py",
        "--queue-db", str(queue_db),
        "--outbox-dir", str(outbox),
        "status",
    )
    assert status.returncode == 0, status.stderr
    status_payload = json.loads(status.stdout)
    assert status_payload["queue"] == {"pending": 1}
    assert status_payload["outbox"] == {
        "failed": 0, "pending": 0, "processing": 0, "sent": 0
    }


def test_cindy_control_rejects_non_http_input(tmp_path):
    result = _run(
        "scripts/cindy_control.py",
        "--queue-db", str(tmp_path / ".100x_v3" / "queue.db"),
        "enqueue-url", "not-a-url",
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"] == "invalid_http_url"


def test_outbox_cli_ack_records_structured_receipt(tmp_path):
    root = tmp_path / "outbox"
    box = WechatOutbox(root)
    box.enqueue(OutboxItem(
        event_id="fixture-1",
        lane="business",
        text="fixture",
        url="https://example.com/fixture",
        final_score=0.8,
        business_story_fit=0.8,
        prompt_hash="hash",
        created_at="2026-08-11T14:00:00+00:00",
        expires_at=ttl_for_lane("business"),
        attempts=0,
    ))
    claim = _run(
        "scripts/wechat_outbox.py", "--queue-dir", str(root), "claim", "--limit", "1"
    )
    assert claim.returncode == 0, claim.stderr

    ack = _run(
        "scripts/wechat_outbox.py", "--queue-dir", str(root), "ack", "fixture-1",
        "--agent-kind", "claude-code",
        "--tool", "cindy_wechat.send_message_to_user",
        "--session-ref", "local-session",
        "--recipient-ref", "local-peer",
        "--started-at", "2026-08-11T14:00:00+00:00",
        "--finished-at", "2026-08-11T14:00:01+00:00",
        "--message-id", "message-1",
        "--raw-response", '{"ok":true,"token":"secret"}',
    )
    assert ack.returncode == 0, ack.stderr
    payload = json.loads((root / "sent" / "fixture-1.json").read_text(encoding="utf-8"))
    receipt = payload["delivery_attempts"][0]["receipt"]
    assert receipt["message_id"] == "message-1"
    assert receipt["raw_response"]["token"] == "[REDACTED]"
