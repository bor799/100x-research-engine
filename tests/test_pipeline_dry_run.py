import json
from pathlib import Path

from knowledge_extractor_v3.llm.provider import StubLLMProvider
from knowledge_extractor_v3.llm.shadow import ShadowHeuristicLLMProvider
from knowledge_extractor_v3.models import FetchedContent, RuntimeMode, ScoreResult, sha256_text
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


def test_pipeline_hard_reject_even_with_gate_disabled(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(
        store,
        staging_root=tmp_path / "staging",
        score_gate_enabled=False,
        allow_test_provider=True,
    )

    result = pipeline.process_url("fixture://low_quality", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.REJECTED
    assert result.failure_kind is FailureKind.VALIDATION_FAILED
    assert result.next_action is NextAction.DROP
    assert result.extraction_result is None
    assert result.score_result is not None
    assert result.score_result.signal_tier == "Reject"
    route_stage = next(stage for stage in result.stage_results if stage.stage == "routing")
    assert route_stage.detail.get("route") == "reject"


class FixedScoreLLMProvider(StubLLMProvider):
    model_route = "test://fixed-score"

    def __init__(
        self,
        *,
        score: float,
        final_score: float,
        signal_tier: str = "B",
        l_dims: tuple[float, float, float] = (0.7, 0.7, 0.7),
    ) -> None:
        self._score = score
        self._final_score = final_score
        self._signal_tier = signal_tier
        self._l_dims = l_dims
        self.extract_calls = 0

    def score(self, content: FetchedContent, prompt: str) -> str:
        return json.dumps(
            {
                "score": self._score,
                "final_score": self._final_score,
                "signal_tier": self._signal_tier,
                "L1": self._l_dims[0],
                "L2": self._l_dims[1],
                "L3": self._l_dims[2],
                "L4": self._final_score,
                "objective_quality": 0.343,
                "decision_window_status": "open",
                "source_type": content.source_type,
                "source_tier": "primary",
                "interest_flag": "track",
                "attribution_chain": [content.source, content.url],
                "rationale": "Fixed score for extraction gate regression tests.",
                "key_claims": ["claim"],
                "watch_items": ["watch"],
            }
        )

    def extract(self, content: FetchedContent, score: ScoreResult, prompt: str) -> str:
        self.extract_calls += 1
        return super().extract(content, score, prompt)


def test_pipeline_archives_band_content_and_rejects_below_floor_before_extraction(tmp_path):
    """Since the 2026-08-16 recalibration, 0.5-0.69 final_score is archive
    material under the linear scoring formula (extracted + stored in Obsidian,
    never pushed), while content below the 0.55 archive floor is still rejected
    at routing, before any extraction tokens are spent."""
    # 0.69: archive band -> extracted, done, no push lane.
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    llm = FixedScoreLLMProvider(score=6.9, final_score=0.69, signal_tier="B")
    pipeline = Pipeline(
        store,
        llm_provider=llm,
        staging_root=tmp_path / "staging",
        score_gate_enabled=False,
        allow_test_provider=True,
    )

    result = pipeline.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.DONE
    assert result.extraction_result is not None
    assert llm.extract_calls == 1
    route_stage = next(stage for stage in result.stage_results if stage.stage == "routing")
    assert route_stage.detail.get("route") == "archive_only"

    # 0.40 (L-dims low so the parser's linear recompute also lands below the
    # 0.55 floor): below the archive floor -> rejected before extraction.
    store2 = QueueStore(tmp_path / ".100x_v3b" / "queue.db", runtime_fingerprint="test-fp")
    llm2 = FixedScoreLLMProvider(score=4.0, final_score=0.40, signal_tier="C", l_dims=(0.4, 0.4, 0.4))
    pipeline2 = Pipeline(
        store2,
        llm_provider=llm2,
        staging_root=tmp_path / "staging2",
        score_gate_enabled=False,
        allow_test_provider=True,
    )
    result2 = pipeline2.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)
    assert result2.final_status is QueueStatus.REJECTED
    assert result2.failure_kind is FailureKind.VALIDATION_FAILED
    assert result2.next_action is NextAction.DROP
    assert result2.extraction_result is None
    assert llm2.extract_calls == 0
    route_stage2 = next(stage for stage in result2.stage_results if stage.stage == "routing")
    assert route_stage2.detail.get("route") == "reject"


def test_pipeline_allows_score_equal_to_seven(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    llm = FixedScoreLLMProvider(score=7.0, final_score=0.7, signal_tier="A")
    pipeline = Pipeline(
        store,
        llm_provider=llm,
        staging_root=tmp_path / "staging",
        allow_test_provider=True,
    )

    result = pipeline.process_url("fixture://high_signal", source="rss", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.DONE
    assert result.failure_kind is FailureKind.NONE
    assert result.extraction_result is not None
    assert llm.extract_calls == 1


def test_pipeline_dry_run_parse_error_is_terminal(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url("fixture://parse_error", mode=RuntimeMode.DRY_RUN)

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.PARSE_ERROR
    assert result.next_action is NextAction.INVESTIGATE
    assert result.output_path == ""
    assert result.current_stage == "score_parse"


def test_pipeline_rejects_incompatible_prompt_before_llm_call(tmp_path):
    pipeline = _pipeline(tmp_path)

    result = pipeline.process_url(
        "fixture://high_signal",
        mode=RuntimeMode.DRY_RUN,
        prompt_bundle="v2_legacy",
    )

    assert result.final_status is QueueStatus.FAILED_TERMINAL
    assert result.failure_kind is FailureKind.PROMPT_CONTRACT
    assert result.current_stage == "resolve_prompt_bundle"
    assert "incompatible with the V3 parser contract" in result.error.detail


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
    by_bundle = {item.prompt_bundle: item for item in result.parallel_results}
    legacy = by_bundle.pop("v2_legacy")
    assert not legacy.ok
    assert legacy.error is not None
    assert legacy.error.failure_kind is FailureKind.PROMPT_CONTRACT
    assert all(item.ok for item in by_bundle.values())
    assert all(item.prompt_hash for item in by_bundle.values())
    assert result.output_path.startswith("dry-run://")


class FundingSignalFetcher:
    def fetch(self, url: str) -> FetchedContent:
        text = (
            "Frontier Payments raised a $24 million Series A led by named investors, "
            "with a disclosed valuation range and three customer deployment examples. "
            "The company says the new capital will expand fraud-risk infrastructure for "
            "AI-native merchants, and the article names two pilot customers, the payment "
            "volume baseline, hiring plans, and the specific market wedge. Independent "
            "investor comments explain why the round is happening now, citing chargeback "
            "growth, compliance pressure, and a distribution partnership already live in "
            "North America. The source includes enough concrete financing, customer, and "
            "operating evidence to update a primary-market watchlist immediately."
        )
        return FetchedContent(
            url=url,
            source="fixture",
            source_type="web_article",
            title="Frontier Payments raises Series A for AI merchant fraud infrastructure",
            text=text,
            fetched_at="2026-05-31T00:00:00+00:00",
            content_hash=sha256_text(text),
        )


def test_pipeline_can_run_explicit_rimbo_prompt_bundle(tmp_path):
    store = QueueStore(tmp_path / ".100x_v3" / "queue.db", runtime_fingerprint="test-fp")
    pipeline = Pipeline(
        store,
        fetcher=FundingSignalFetcher(),
        llm_provider=ShadowHeuristicLLMProvider(),
        staging_root=tmp_path / "staging",
        allow_test_provider=True,
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
