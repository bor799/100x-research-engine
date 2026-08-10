"""Tests for the V3 health-check efficiency fixes.

Covers:
  - queue_status uses a 24h rolling window, not a permanent cumulative count
  - config_drift surfaces when the running fingerprint is stale relative to disk
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from knowledge_extractor_v3.config_loader import V3Config
from knowledge_extractor_v3.health import HealthChecker, HealthStatus
from knowledge_extractor_v3.queue_store import QueueStore


def _config(state_root: Path) -> V3Config:
    import dataclasses

    base = V3Config()
    return dataclasses.replace(
        base,
        runtime=dataclasses.replace(base.runtime, state_root=str(state_root)),
    )


def _seed_task(store: QueueStore, *, url: str, status: str, hours_ago: int) -> None:
    """Insert a task and rewind its updated_at to simulate age."""
    task = store.enqueue(url, source="test")
    from datetime import datetime, timezone

    old = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    with __import__("sqlite3").connect(store.db_path) as conn:
        conn.execute(
            "UPDATE queue SET status=?, updated_at=? WHERE id=?",
            (status, old, task.id),
        )
        conn.commit()


def test_queue_status_uses_24h_window_not_cumulative(tmp_path):
    """663 lifetime failures must not hold the system in warning forever once
    they age out of the 24h window."""
    store = QueueStore(tmp_path / "queue.db")
    store.initialize()
    checker = HealthChecker(_config(tmp_path), queue_store=store)

    # Seed many old failures (outside the 24h window).
    for i in range(20):
        _seed_task(store, url=f"https://old/{i}", status="failed_terminal", hours_ago=48)

    check = checker._check_queue_status()
    # Old failures alone must not trip the warning.
    assert check.status is HealthStatus.HEALTHY
    assert check.detail["failed_terminal"] == 20  # cumulative still surfaced
    assert check.detail["recent_24h"].get("failed_terminal", 0) == 0


def test_queue_status_warns_on_recent_failures(tmp_path):
    store = QueueStore(tmp_path / "queue.db")
    store.initialize()
    checker = HealthChecker(_config(tmp_path), queue_store=store)

    for i in range(15):
        _seed_task(store, url=f"https://new/{i}", status="failed_terminal", hours_ago=2)

    check = checker._check_queue_status()
    assert check.status is HealthStatus.WARNING
    assert check.detail["recent_24h"]["failed_terminal"] == 15


def test_config_drift_detects_stale_fingerprint(tmp_path):
    """When the on-disk fingerprint's source_hash differs from the current one,
    health must report ERROR (the 'false green' this check exists to catch)."""
    config = _config(tmp_path)

    # Write a fingerprint with a fake source_hash that will not match reality.
    fingerprint = {
        "created_at": "2026-08-10T01:19:53+00:00",
        "source_hash": "stale_source_hash_that_does_not_match_current",
        "active_bundle": "v3_business_stories",
        "active_prompt_hash": "deadbeefdeadbeef",
    }
    (tmp_path / "runtime_fingerprint.json").write_text(
        json.dumps(fingerprint), encoding="utf-8"
    )

    # Build a guard whose build_fingerprint returns the CURRENT (different) hash.
    class FakeGuard:
        class _Fingerprint:
            active_bundle = "v3_business_stories"
            active_prompt_hash = "current_prompt_hash_aa"
            source_hash = "current_source_hash_bb"

        def build_fingerprint(self):
            return self._Fingerprint()

    checker = HealthChecker(config, runtime_guard=FakeGuard())
    check = checker._check_config_drift()
    assert check.status is HealthStatus.ERROR
    assert "source code changed" in check.message or "prompt bundle drifted" in check.message


def test_config_drift_healthy_when_no_fingerprint(tmp_path):
    config = _config(tmp_path)
    checker = HealthChecker(config)
    check = checker._check_config_drift()
    assert check.status is HealthStatus.HEALTHY
