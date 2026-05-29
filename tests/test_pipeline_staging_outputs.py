from pathlib import Path

from knowledge_extractor_v3.models import RuntimeMode, sha256_text
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.queue_store import FailureKind, QueueStatus, QueueStore


def _pipeline(tmp_path: Path) -> Pipeline:
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    return Pipeline(store, staging_root=tmp_path / "staging")


def test_pipeline_staging_writes_obsidian_and_telegram_stub(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://high_signal", source="telegram", mode=RuntimeMode.STAGING)

    assert result.final_status is QueueStatus.DONE
    output_path = Path(result.output_path)
    assert output_path.exists()
    assert output_path.is_relative_to(tmp_path / "staging" / "obsidian")
    markdown = output_path.read_text(encoding="utf-8")
    assert f"prompt_bundle: \"{pipeline.prompt_registry.active_bundle_name}\"" in markdown
    scoring_prompt = pipeline.prompt_registry.load_prompt(pipeline.prompt_registry.active_bundle_name, "scoring")
    extraction_prompt = pipeline.prompt_registry.load_prompt(pipeline.prompt_registry.active_bundle_name, "extraction")
    telegram_prompt = pipeline.prompt_registry.load_prompt(pipeline.prompt_registry.active_bundle_name, "telegram_brief")
    expected_hash = sha256_text(scoring_prompt + extraction_prompt + telegram_prompt, length=16)
    assert f"prompt_hash: \"{expected_hash}\"" in markdown
    assert "signal_tier: \"A\"" in markdown
    assert "fixture://high_signal" in markdown

    telegram_log = tmp_path / "staging" / "telegram_stub.log"
    assert telegram_log.exists()
    assert "Frontier Payments API Finds Bottom-Up Distribution" in telegram_log.read_text(
        encoding="utf-8"
    )


def test_pipeline_staging_reject_does_not_write_outputs(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://low_quality", mode=RuntimeMode.STAGING)

    assert result.final_status is QueueStatus.REJECTED
    assert result.output_path == ""
    assert not (tmp_path / "staging").exists()


def test_pipeline_staging_output_failure_is_terminal_without_done_path(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://output_failed", mode=RuntimeMode.STAGING)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.OUTPUT_FAILED
    assert result.output_path == ""
    assert not (tmp_path / "staging" / "obsidian").exists()
