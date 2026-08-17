import json
from pathlib import Path

from knowledge_extractor_v3.llm.provider import StubLLMProvider
from knowledge_extractor_v3.models import FetchedContent, RuntimeMode, ScoreResult, sha256_text
from knowledge_extractor_v3.outputs.live_obsidian import LiveObsidianWriter, LiveOutputPort
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.queue_store import FailureKind, NextAction, QueueStatus, QueueStore


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


def test_pipeline_low_quality_rejects_at_routing(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(store, staging_root=tmp_path / "staging", allow_test_provider=True)

    result = pipeline.process_url("fixture://low_quality", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.REJECTED
    assert result.failure_kind is FailureKind.VALIDATION_FAILED
    assert result.next_action is NextAction.DROP
    assert result.extraction_result is None
    assert result.score_result is not None
    assert result.score_result.signal_tier == "Reject"
    route_stage = next(stage for stage in result.stage_results if stage.stage == "routing")
    assert route_stage.detail.get("route") == "reject"


class AbsorptionProvider(StubLLMProvider):
    """Emits a fixed absorption payload; fails loudly if the legacy extract
    call is ever reached (the V4 pipeline must make exactly one LLM call)."""

    model_route = "test://fixed-absorption"

    def __init__(
        self,
        *,
        gain: float = 0.7,
        action: float = 0.7,
        relevance: float = 0.7,
        is_spam: bool = False,
    ) -> None:
        self._dims = (gain, action, relevance)
        self._is_spam = is_spam
        self.score_calls = 0

    def score(self, content: FetchedContent, prompt: str) -> str:
        self.score_calls += 1
        gain, action, relevance = self._dims
        return json.dumps(
            {
                "information_gain": gain,
                "action_value": action,
                "relevance": relevance,
                "is_spam": self._is_spam,
                "rationale": "Fixed dimensions for routing regression tests.",
                "title": "Fixed absorption result",
                "one_line_summary": "一句话归纳用于路由回归测试。",
                "category": "技术创业",
                "experiences": ["先卖最小付费切片。"],
                "signals": ["小团队高 ARR 越来越常见。"],
                "key_facts": ["3 人做到 200 万 ARR。"],
                "quote": "",
                "next_action": "验证下一个证据点。",
                "obsidian_brief_markdown": "# 存档",
            },
            ensure_ascii=False,
        )

    def extract(self, content: FetchedContent, score: ScoreResult, prompt: str) -> str:
        raise AssertionError("V4 pipeline must not call extract()")


def test_pipeline_archives_band_content_and_drops_below_floor(tmp_path):
    """0.40-0.74 is archive material: absorbed once (a single LLM call),
    archived to Obsidian, never pushed. Below 0.40 drops at routing."""
    # 0.69 dims -> 0.69 final: archive band -> absorbed, done, no push lane.
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    llm = AbsorptionProvider(gain=0.69, action=0.69, relevance=0.69)
    pipeline = Pipeline(
        store, llm_provider=llm, staging_root=tmp_path / "staging", allow_test_provider=True
    )

    result = pipeline.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.DONE
    assert result.extraction_result is not None
    assert llm.score_calls == 1  # single-call pipeline
    route_stage = next(stage for stage in result.stage_results if stage.stage == "routing")
    assert route_stage.detail.get("route") == "archive_only"
    assert result.route == "archive_only"

    # 0.35 dims -> 0.35 final: below the 0.40 floor -> rejected.
    store2 = QueueStore(tmp_path / ".100x_v3b" / "queue.db", runtime_fingerprint="test-fp")
    llm2 = AbsorptionProvider(gain=0.35, action=0.35, relevance=0.35)
    pipeline2 = Pipeline(
        store2, llm_provider=llm2, staging_root=tmp_path / "staging2", allow_test_provider=True
    )
    result2 = pipeline2.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)
    assert result2.final_status is QueueStatus.REJECTED
    assert result2.failure_kind is FailureKind.VALIDATION_FAILED
    assert result2.next_action is NextAction.DROP
    assert result2.extraction_result is None
    route_stage2 = next(stage for stage in result2.stage_results if stage.stage == "routing")
    assert route_stage2.detail.get("route") == "reject"


def test_pipeline_push_band_split_by_action_value(tmp_path):
    """0.75+ pushes; action_value >= 0.70 chooses the daily business lane,
    lower action_value lands on the weekly strategic lane."""
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    llm = AbsorptionProvider(gain=0.75, action=0.80, relevance=0.75)
    pipeline = Pipeline(
        store, llm_provider=llm, staging_root=tmp_path / "staging", allow_test_provider=True
    )

    result = pipeline.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.DONE
    assert result.route == "business_push"
    assert result.score_result.signal_tier == "A"

    store2 = QueueStore(tmp_path / ".100x_v3c" / "queue.db", runtime_fingerprint="test-fp")
    llm2 = AbsorptionProvider(gain=0.95, action=0.60, relevance=0.85)
    pipeline2 = Pipeline(
        store2, llm_provider=llm2, staging_root=tmp_path / "staging2", allow_test_provider=True
    )
    result2 = pipeline2.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)
    assert result2.route == "strategic_digest"


def test_pipeline_spam_drops_even_with_high_dimensions(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    llm = AbsorptionProvider(gain=0.9, action=0.9, relevance=0.9, is_spam=True)
    pipeline = Pipeline(
        store, llm_provider=llm, staging_root=tmp_path / "staging", allow_test_provider=True
    )

    result = pipeline.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.REJECTED
    assert result.score_result.final_score >= 0.75  # dimensions were high…
    assert result.score_result.signal_tier == "Reject"  # …but spam forces Reject


def test_pipeline_dry_run_parse_error_is_terminal(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://parse_error", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.PARSE_ERROR
    assert result.next_action is NextAction.INVESTIGATE
    assert result.output_path == ""
    assert result.current_stage == "absorb_parse"


def test_pipeline_missing_absorption_prompt_fails_fast(tmp_path):
    from knowledge_extractor_v3.absorption_prompt import AbsorptionPromptError

    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    try:
        Pipeline(store, staging_root=tmp_path / "staging", allow_test_provider=True)
    except AbsorptionPromptError:
        raise AssertionError("repo ships prompts/absorption.md; load must succeed")


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
    live_port = LiveOutputPort(obsidian_writer=writer)

    pipeline = Pipeline(store, staging_root=tmp_path / "staging", live_output=live_port)

    result = pipeline.process_url("fixture://high_signal", mode=RuntimeMode.LIVE)

    assert result.final_status is QueueStatus.DONE
    assert result.failure_kind is FailureKind.NONE
    assert result.output_path
    assert Path(result.output_path).exists()
    assert str(result.output_path).startswith(str(obsidian_root))


class PaywallShellFetcher:
    def fetch(self, url: str) -> FetchedContent:
        text = "Subscribe to continue reading this article. Already a subscriber? Sign in to access the full story."
        return FetchedContent(
            url=url,
            source="agent-reach-web",
            source_type="web_article",
            title="The Economist: Major AI Breakthrough",
            text=text,
            fetched_at="2026-05-31T00:00:00+00:00",
            content_hash=sha256_text(text),
        )


def test_pipeline_rejects_paywall_shell_at_validate(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(store, fetcher=PaywallShellFetcher(), staging_root=tmp_path / "staging", allow_test_provider=True)

    result = pipeline.process_url("https://www.economist.com/example", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.CONTENT_BLOCKED
    assert result.current_stage == "validate"
    assert result.extraction_result is None


class TitleOnlyFetcher:
    def fetch(self, url: str) -> FetchedContent:
        text = "Breaking: Major AI Breakthrough"
        return FetchedContent(
            url=url,
            source="rss",
            source_type="rss_feed",
            title="Breaking: Major AI Breakthrough",
            text=text,
            fetched_at="2026-05-31T00:00:00+00:00",
            content_hash=sha256_text(text),
        )


def test_pipeline_rejects_title_only_article(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(store, fetcher=TitleOnlyFetcher(), staging_root=tmp_path / "staging", allow_test_provider=True)

    result = pipeline.process_url("https://example.com/article", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.CONTENT_BLOCKED
    assert result.current_stage == "validate"
    assert result.extraction_result is None


class ShortTweetFetcher:
    def fetch(self, url: str) -> FetchedContent:
        text = "Great insight on AI agents! Building autonomous systems is the future."
        return FetchedContent(
            url=url,
            source="agent-reach-twitter",
            source_type="twitter_thread",
            title="Tweet",
            text=text,
            fetched_at="2026-05-31T00:00:00+00:00",
            content_hash=sha256_text(text),
        )


def test_pipeline_allows_short_non_article_content(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(store, fetcher=ShortTweetFetcher(), staging_root=tmp_path / "staging", allow_test_provider=True)

    result = pipeline.process_url("https://x.com/user/status/123", mode=RuntimeMode.DRY_RUN)

    assert result.current_stage == "done"
    assert result.extraction_result is not None
