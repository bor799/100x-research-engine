"""Tests for queue worker with temporary queue database."""

import json
import os
import pytest
import signal
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

PROJECT_ROOT = Path(__file__).parents[1]

from knowledge_extractor_v3.worker import (
    LiveModeUnavailable,
    QueueWorker,
    WorkerConfig,
    WorkerRunResult,
    WorkerState,
    create_worker,
)
from knowledge_extractor_v3.queue_store import QueueStore, QueueStatus
from knowledge_extractor_v3.config_loader import (
    AgentReachConfig,
    V3Config,
    LiveConfig,
    RuntimeConfig,
    ScoreGateConfig,
    WorkerConfig as V3WorkerConfig,
)
from knowledge_extractor_v3.models import RuntimeMode
from knowledge_extractor_v3.fetchers.fixture import FixtureFetcher
from knowledge_extractor_v3.fetchers.multi_channel import AgentReachFetcher
from knowledge_extractor_v3.fetchers.router import FetcherRouter
from knowledge_extractor_v3.prompt_registry import PromptRegistry


class CapturingReplyClient:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def deliver(self, content, text: str, *, chat_id: str | None = None):
        self.messages.append({"chat_id": chat_id or "", "text": text})
        return "sent", text[:80]


def make_test_config(state_root: Path) -> V3Config:
    """Create a minimal test config."""
    return V3Config(
        runtime=RuntimeConfig(
            state_root=str(state_root),
            queue_db_path=str(state_root / "queue.db"),
            log_path=str(state_root / "worker.jsonl"),
        ),
        live=LiveConfig(enabled=False, max_consecutive_failures=5),
        worker=V3WorkerConfig(batch_size=10, poll_interval_seconds=30),
        agent_reach=AgentReachConfig(enabled=False),
    )


def test_worker_state_record_success():
    """WorkerState records success correctly."""
    state = WorkerState()

    assert state.consecutive_failures == 0
    assert state.total_processed == 0

    state.record_success()

    assert state.consecutive_failures == 0
    assert state.total_processed == 1
    assert state.total_succeeded == 1


def test_worker_state_record_failure():
    """WorkerState records failure correctly."""
    state = WorkerState()

    state.record_failure()
    assert state.consecutive_failures == 1
    assert state.total_processed == 1
    assert state.total_failed == 1

    state.record_failure()
    assert state.consecutive_failures == 2

    state.record_success()
    assert state.consecutive_failures == 0  # Reset on success


def test_worker_recover_stale_tasks():
    """Worker recovers stale processing tasks."""
    from datetime import datetime, timedelta, UTC
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db", runtime_fingerprint="test-fp")

        # Enqueue a task and mark it processing
        task = queue_store.enqueue("https://example.com/test", source="test")
        queue_store.mark_processing(task.id)

        # Verify it's processing
        assert queue_store.get_task(task.id).status == QueueStatus.PROCESSING

        # Create worker and run recovery with a future timestamp to recover all
        worker_cfg = WorkerConfig(
            batch_size=10,
            processing_stale_after_minutes=0,  # Immediate recovery
        )
        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            worker_config=worker_cfg,
        )

        # Use a far future timestamp to ensure recovery happens
        future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        recovered = queue_store.recover_stale_processing(future)

        assert recovered == 1
        assert queue_store.get_task(task.id).status == QueueStatus.RETRY_SCHEDULED


def test_worker_run_once_empty_queue():
    """Worker handles empty queue gracefully."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        worker_cfg = WorkerConfig(batch_size=10)
        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            worker_config=worker_cfg,
        )

        result = worker.run_once()

        assert result.tasks_processed == 0
        assert result.tasks_succeeded == 0
        assert result.tasks_failed == 0
        assert result.consecutive_failures == 0


def test_worker_wait_uses_timed_sleep(monkeypatch):
    """Worker loop wait should wake by timeout instead of blocking on a signal."""
    config = make_test_config(Path(tempfile.mkdtemp()))
    queue_store = QueueStore(Path(tempfile.mkdtemp()) / "queue.db")
    worker = QueueWorker(config=config, queue_store=queue_store)
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        worker._state.shutdown_requested = True

    monkeypatch.setattr("knowledge_extractor_v3.worker.time.sleep", fake_sleep)

    worker._wait(30)

    assert sleeps
    assert sleeps[0] <= 1.0


def test_worker_run_once_successful_task():
    """Worker processes a single successful task (with stub fetcher)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        # Enqueue a fixture URL
        task = queue_store.enqueue("fixture://high_signal", source="test")

        worker_cfg = WorkerConfig(batch_size=10)
        # Use fixture fetcher for test URLs
        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            worker_config=worker_cfg,
        )

        result = worker.run_once()

        assert result.tasks_processed == 1
        assert result.tasks_succeeded == 1
        assert result.tasks_failed == 0

        # Verify task is done
        updated = queue_store.get_task(task.id)
        assert updated.status == QueueStatus.DONE
        assert updated.output_path != ""


def test_worker_force_extracts_score_reject_when_score_gate_disabled():
    """Worker must pass score_gate.enabled=false through to Pipeline."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        config = V3Config(
            runtime=config.runtime,
            live=config.live,
            worker=config.worker,
            agent_reach=config.agent_reach,
            score_gate=ScoreGateConfig(enabled=False),
        )
        queue_store = QueueStore(state_root / "queue.db")
        task = queue_store.enqueue("fixture://low_quality", source="test")

        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            worker_config=WorkerConfig(batch_size=10),
        )

        result = worker.run_once()

        assert result.tasks_processed == 1
        assert result.tasks_succeeded == 1
        updated = queue_store.get_task(task.id)
        assert updated.status == QueueStatus.DONE
        assert updated.output_path
        assert Path(updated.output_path).exists()


def test_worker_notifies_telegram_reply_chat_on_terminal_failure():
    """Manual Telegram tasks receive a closing failure message."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")
        queue_store.enqueue(
            "fixture://fetch_failed",
            source="telegram_bot",
            priority=10,
            reply_channel="telegram",
            reply_chat_id="chat-123",
        )
        reply_client = CapturingReplyClient()

        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            reply_telegram_client=reply_client,
        )

        result = worker.run_once()

        assert result.tasks_failed == 1
        assert len(reply_client.messages) == 1
        assert reply_client.messages[0]["chat_id"] == "chat-123"
        assert "Failed (ID:" in reply_client.messages[0]["text"]
        assert "Fixture fetch failed" in reply_client.messages[0]["text"]


def test_worker_live_mode_does_not_silently_fall_back_to_staging():
    """A requested live worker should fail loudly when the live gate is closed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")
        queue_store.enqueue("fixture://high_signal", source="test")
        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            worker_config=WorkerConfig(mode=RuntimeMode.LIVE),
        )

        with pytest.raises(LiveModeUnavailable):
            worker.run_once()


def test_worker_live_gate_loader_is_loaded_before_check(tmp_path, monkeypatch):
    """Regression: worker-created ConfigLoader must know config.local.yaml was used."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.local.yaml").write_text(
        "runtime:\n  queue_db_path: \"queue.db\"\n",
        encoding="utf-8",
    )

    class InspectingGate:
        def __init__(self, config, *, config_loader, runtime_guard):
            assert config_loader.using_local_config is True

        def check(self):
            class Result:
                passed = False
                rejection_reasons = ["expected test rejection"]

            return Result()

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("knowledge_extractor_v3.worker.LiveGate", InspectingGate)
    monkeypatch.setattr("knowledge_extractor_v3.worker.RuntimeGuard.from_env", lambda project_root: object())

    config = V3Config(
        runtime=RuntimeConfig(
            state_root=str(tmp_path),
            queue_db_path=str(tmp_path / "queue.db"),
        ),
        live=LiveConfig(enabled=True),
        worker=V3WorkerConfig(batch_size=1),
    )
    queue_store = QueueStore(tmp_path / "queue.db")
    queue_store.enqueue("fixture://high_signal", source="test")
    worker = QueueWorker(
        config=config,
        queue_store=queue_store,
        fetcher=FixtureFetcher(),
        prompt_registry=PromptRegistry.default(PROJECT_ROOT),
        worker_config=WorkerConfig(mode=RuntimeMode.LIVE),
    )

    with pytest.raises(LiveModeUnavailable, match="expected test rejection"):
        worker.run_once()


def test_worker_run_once_respects_batch_size():
    """Worker respects batch_size limit."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        # Enqueue multiple tasks
        for i in range(5):
            queue_store.enqueue(f"fixture://high_signal_{i}", source="test")

        worker_cfg = WorkerConfig(batch_size=3)
        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            worker_config=worker_cfg,
        )

        result = worker.run_once()

        assert result.tasks_processed == 3  # Only batch_size processed


def test_worker_stops_after_max_consecutive_failures():
    """Worker stops when consecutive failures reach threshold."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        # Enqueue tasks that will fail (bad URLs)
        for i in range(10):
            queue_store.enqueue(f"fixture://llm_timeout_{i}", source="test")

        worker_cfg = WorkerConfig(
            batch_size=10,
            max_consecutive_failures=3,
        )
        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            worker_config=worker_cfg,
        )

        result = worker.run_once()

        # Should stop after 3 consecutive failures
        assert result.consecutive_failures >= 3
        assert result.tasks_processed >= 3
        assert result.should_stop is True


def test_worker_stops_batch_on_llm_rate_limit():
    """Rate limits should pause the worker before burning through the queue."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        queue_store.enqueue("fixture://llm_rate_limit", source="test")
        queue_store.enqueue("fixture://high_signal", source="test")
        queue_store.enqueue("fixture://low_quality", source="test")

        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            worker_config=WorkerConfig(batch_size=10, max_consecutive_failures=5),
        )

        result = worker.run_once()

        assert result.tasks_processed == 1
        assert result.should_stop is True
        counts = queue_store.count_by_status()
        assert counts.get("pending", 0) == 2


def test_worker_next_ready_tasks_priority_order():
    """QueueStore.next_ready_tasks respects priority order."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        queue_store = QueueStore(state_root / "queue.db")

        # Enqueue tasks with different priorities
        queue_store.enqueue("url://low", source="test", priority=100)
        queue_store.enqueue("url://high", source="test", priority=10)
        queue_store.enqueue("url://mid", source="test", priority=50)

        tasks = queue_store.next_ready_tasks(limit=10)

        # Should be ordered by priority (lower first)
        assert tasks[0].url == "url://high"
        assert tasks[1].url == "url://mid"
        assert tasks[2].url == "url://low"


def test_worker_next_ready_tasks_respects_limit():
    """QueueStore.next_ready_tasks respects limit parameter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        queue_store = QueueStore(state_root / "queue.db")

        for i in range(10):
            queue_store.enqueue(f"url://{i}", source="test")

        tasks = queue_store.next_ready_tasks(limit=5)

        assert len(tasks) == 5


def test_queue_store_count_by_status():
    """QueueStore.count_by_status returns correct counts."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        queue_store = QueueStore(state_root / "queue.db")

        queue_store.enqueue("url://1", source="test")
        queue_store.enqueue("url://2", source="test")
        queue_store.enqueue("url://3", source="test")

        # Mark one as processing
        task1 = queue_store.find_by_url("url://1")
        queue_store.mark_processing(task1.id)  # type: ignore

        counts = queue_store.count_by_status()

        assert counts.get("pending", 0) == 2
        assert counts.get("processing", 0) == 1


def test_queue_store_find_by_url():
    """QueueStore.find_by_url finds task by normalized URL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        queue_store = QueueStore(state_root / "queue.db")

        queue_store.enqueue("  https://example.com/test  ", source="test")

        task = queue_store.find_by_url("  https://example.com/test  ")

        assert task is not None
        # URL is normalized (stripped) when stored
        assert "https://example.com/test" in task.url

        # Should not find different URL
        assert queue_store.find_by_url("https://example.com/other") is None


def test_worker_jsonl_logging():
    """Worker writes JSONL log when configured."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        log_path = state_root / "worker.jsonl"
        queue_store = QueueStore(state_root / "queue.db")

        queue_store.enqueue("fixture://high_signal", source="test")

        worker_cfg = WorkerConfig(batch_size=10, log_jsonl=True)
        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            fetcher=FixtureFetcher(),
            worker_config=worker_cfg,
            log_path=log_path,
        )

        worker.run_once()

        # Check log file exists and has one entry
        assert log_path.exists()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert "url" in entry
        assert "final_status" in entry
        assert entry["final_status"] == "done"


def test_create_worker_helper():
    """create_worker helper creates worker from config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)

        worker = create_worker(config, mode=RuntimeMode.STAGING)

        assert worker is not None
        assert isinstance(worker, QueueWorker)


def test_worker_uses_agent_reach_config_by_default():
    """Worker wires configured Agent Reach settings into FetcherRouter."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        agent_config = state_root / "agent-reach.yaml"
        agent_config.write_text("twitter:\n  auth_token: token\n  ct0: csrf\n", encoding="utf-8")
        config = V3Config(
            runtime=RuntimeConfig(
                state_root=str(state_root),
                queue_db_path=str(state_root / "queue.db"),
                log_path=str(state_root / "worker.jsonl"),
            ),
            agent_reach=AgentReachConfig(
                enabled=True,
                config_path=str(agent_config),
                enabled_channels=["twitter"],
                fallback_to_jina=False,
                proxy="http://127.0.0.1:7890",
            ),
        )

        worker = QueueWorker(
            config=config,
            queue_store=QueueStore(state_root / "queue.db"),
        )

        assert isinstance(worker._fetcher, FetcherRouter)
        agent = worker._fetcher.agent_reach_fetcher
        assert isinstance(agent, AgentReachFetcher)
        assert agent.config_path == agent_config
        assert agent.enabled_channels == ["twitter"]
        assert agent.fallback_to_jina is False
        assert agent.base_proxy == "http://127.0.0.1:7890"


def test_worker_signal_handler_sets_shutdown_flag():
    """Worker signal handler sets shutdown_requested."""
    state = WorkerState()
    assert state.shutdown_requested is False

    # Simulate signal handler
    from knowledge_extractor_v3.worker import QueueWorker
    worker = QueueWorker(
        config=make_test_config(Path(tempfile.mkdtemp())),
        queue_store=QueueStore(Path(tempfile.mkdtemp()) / "queue.db"),
    )
    worker._handle_shutdown(signal.SIGINT, None)

    assert worker._state.shutdown_requested is True


def test_worker_guard_does_not_leave_task_in_processing():
    """Regression: real URL + stub provider must not leave task stuck in PROCESSING.

    The provider guard in Pipeline blocks test providers from processing real URLs.
    Before the fix, the worker marked the task PROCESSING first, then the pipeline
    guard returned early without resolving the queue task — leaving it orphaned.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        state_root = Path(tmpdir)
        config = make_test_config(state_root)
        queue_store = QueueStore(state_root / "queue.db")

        # Enqueue a real (non-fixture) URL
        task = queue_store.enqueue("https://example.com/article", source="test")

        worker = QueueWorker(
            config=config,
            queue_store=queue_store,
            # Default _llm is StubLLMProvider — exactly the scenario that triggers the guard
        )

        result = worker.run_once()

        # The task should be resolved, not stuck in PROCESSING
        updated = queue_store.get_task(task.id)
        assert updated.status == QueueStatus.FAILED_TERMINAL, (
            f"Expected FAILED_TERMINAL, got {updated.status}"
        )
        assert updated.failure_kind == "runtime_guard"
        assert result.tasks_failed == 1


if __name__ == "__main__":
    import traceback
    import signal

    tests = [
        test_worker_state_record_success,
        test_worker_state_record_failure,
        test_worker_recover_stale_tasks,
        test_worker_run_once_empty_queue,
        test_worker_run_once_successful_task,
        test_worker_run_once_respects_batch_size,
        test_worker_stops_after_max_consecutive_failures,
        test_worker_next_ready_tasks_priority_order,
        test_worker_next_ready_tasks_respects_limit,
        test_queue_store_count_by_status,
        test_queue_store_find_by_url,
        test_worker_jsonl_logging,
        test_create_worker_helper,
        test_worker_signal_handler_sets_shutdown_flag,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
            print(f"✓ {test.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"✗ {test.__name__}")
            traceback.print_exc()
        except Exception as e:
            failed += 1
            print(f"✗ {test.__name__}: {e}")
            traceback.print_exc()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)
