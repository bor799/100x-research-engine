from pathlib import Path

from knowledge_extractor_v3.llm.shadow import ShadowHeuristicLLMProvider
from knowledge_extractor_v3.models import FetchedContent, RuntimeMode, sha256_text
from knowledge_extractor_v3.outputs.live_obsidian import LiveObsidianWriter, LiveOutputPort
from knowledge_extractor_v3.outputs.telegram_live import LiveTelegramClient
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.queue_store import FailureKind, NextAction, QueueStatus, QueueStore
from tests.test_live_output_port import _mock_http_post


def _pipeline(tmp_path: Path) -> Pipeline:
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    return Pipeline(store, staging_root=tmp_path / "staging", allow_test_provider=True)


def test_pipeline_dry_run_high_signal_marks_done_without_file_output(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://high_signal", source="manual", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.DONE
    assert result.failure_kind is FailureKind.NONE
    assert result.output_path.startswith("dry-run://")
    assert result.score_result is not None
    assert result.extraction_result is not None
    assert result.telegram_status == "stubbed"
    assert not (tmp_path / "staging").exists()


def test_pipeline_dry_run_low_quality_is_rejected_without_output(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://low_quality", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.REJECTED
    assert result.failure_kind is FailureKind.VALIDATION_FAILED
    assert result.next_action is NextAction.DROP
    assert result.output_path == ""
    assert result.extraction_result is None


def test_pipeline_can_force_extract_score_reject(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(
        store,
        staging_root=tmp_path / "staging",
        score_gate_enabled=False,
        allow_test_provider=True,
    )

    result = pipeline.process_url("fixture://low_quality", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.DONE
    assert result.failure_kind is FailureKind.NONE
    assert result.output_path.startswith("dry-run://")
    assert result.score_result is not None
    assert result.score_result.signal_tier == "Reject"
    assert result.extraction_result is not None
    score_gate = next(stage for stage in result.stage_results if stage.stage == "score_gate")
    assert score_gate.detail["gate_enabled"] is False
    assert score_gate.detail["forced_extract"] is True


def test_pipeline_dry_run_parse_error_is_terminal(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://parse_error", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.PARSE_ERROR
    assert result.next_action is NextAction.INVESTIGATE
    assert result.output_path == ""
    assert result.current_stage == "score_parse"


def test_pipeline_dry_run_rate_limit_schedules_retry(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://llm_rate_limit", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.RETRY_SCHEDULED
    assert result.failure_kind is FailureKind.LLM_RATE_LIMIT
    assert result.next_action is NextAction.RETRY_LATER
    assert result.retryable
    task = pipeline.queue_store.get_task(result.queue_task_id)
    assert task.next_retry_at


def test_pipeline_dry_run_timeout_schedules_retry(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://llm_timeout", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.RETRY_SCHEDULED
    assert result.failure_kind is FailureKind.LLM_TIMEOUT
    assert result.retryable


def test_pipeline_dry_run_fetch_failed_is_not_done(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://fetch_failed", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.FETCH_FAILED
    assert result.output_path == ""


class BlockedWechatFetcher:
    def fetch(self, url: str) -> FetchedContent:
        text = "## 环境异常\n\n当前环境异常，完成验证后即可继续访问。\n\n去验证"
        return FetchedContent(
            url=url,
            source="agent-reach-wechat",
            source_type="wechat_article",
            title="Weixin Official Accounts Platform",
            text=text,
            fetched_at="2026-05-06T00:00:00+00:00",
            content_hash=sha256_text(text),
        )


def test_pipeline_rejects_wechat_verification_page(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(store, fetcher=BlockedWechatFetcher(), staging_root=tmp_path / "staging", allow_test_provider=True)

    result = pipeline.process_url("https://mp.weixin.qq.com/s/example", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.CONTENT_BLOCKED
    assert result.current_stage == "validate"


def test_pipeline_dry_run_output_failed_is_not_done(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://output_failed", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.OUTPUT_FAILED
    assert result.output_path == ""
    assert result.score_result is not None
    assert result.extraction_result is not None


def test_pipeline_dry_run_parallel_bundles_do_not_affect_active_output(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url(
        "fixture://high_signal",
        mode=RuntimeMode.DRY_RUN,
        run_parallel_tests=True,
    )

    assert result.final_status is QueueStatus.DONE
    assert result.prompt_bundle == pipeline.prompt_registry.active_bundle_name
    parallel_bundles = [item.prompt_bundle for item in result.parallel_results]
    expected_parallel_bundles = set(pipeline.prompt_registry.parallel_test_bundle_names)
    expected_parallel_bundles.discard(pipeline.prompt_registry.active_bundle_name)
    assert set(parallel_bundles) == expected_parallel_bundles
    assert all(item.ok for item in result.parallel_results)
    assert all(item.prompt_hash for item in result.parallel_results)
    assert result.output_path.startswith("dry-run://")


def test_pipeline_can_run_explicit_rimbo_prompt_bundle(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(
        store,
        llm_provider=ShadowHeuristicLLMProvider(),
        staging_root=tmp_path / "staging",
    )

    result = pipeline.process_url(
        "fixture://high_signal",
        mode=RuntimeMode.DRY_RUN,
        prompt_bundle="rimbo_source_scored_v3",
    )

    assert result.final_status is QueueStatus.DONE
    assert result.prompt_bundle == "rimbo_source_scored_v3"
    assert result.score_result is not None
    assert result.extraction_result is not None
    assert "source_score" in result.score_result.parsed
    assert "content_compression" in result.extraction_result.parsed


def test_pipeline_live_mode_is_refused_before_queue_write(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://high_signal", mode=RuntimeMode.LIVE)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.RUNTIME_GUARD
    assert not (tmp_path / ".100x_v3" / "queue.db").exists()


def test_pipeline_live_mode_with_live_output_processes(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    obsidian_root = tmp_path / "obsidian"
    writer = LiveObsidianWriter(obsidian_root, subdir="inbox", write_manifest=False)
    telegram = LiveTelegramClient(
        bot_token="test-token",
        chat_id="123",
        enabled=False,
    )
    live_port = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)

    pipeline = Pipeline(store, staging_root=tmp_path / "staging", live_output=live_port)

    result = pipeline.process_url("fixture://high_signal", mode=RuntimeMode.LIVE)

    assert result.final_status is QueueStatus.DONE
    assert result.failure_kind is FailureKind.NONE
    assert result.output_path
    assert Path(result.output_path).exists()
    assert str(result.output_path).startswith(str(obsidian_root))
