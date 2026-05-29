"""Sequential Phase 2 pipeline for dry-run and staging verification."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, TypeVar

from .fetchers.base import Fetcher
from .fetchers.fixture import FixtureFetcher
from .llm.provider import LLMProvider, StubLLMProvider
from .models import (
    ExtractionResult,
    FetchedContent,
    ProcessResult,
    PromptRunResult,
    RuntimeMode,
    ScoreResult,
    StageResult,
    TypedError,
    retry_at,
    sha256_text,
    utc_now,
)
from .outputs.obsidian import DryRunOutputPort, OutputPort, StagingOutputPort
from .prompt_parser import parse_extraction_result, parse_score_result
from .prompt_registry import PromptRegistry
from .queue_store import FailureKind, NextAction, QueueStatus, QueueStore, QueueTask


T = TypeVar("T")


class Pipeline:
    """Run one queue task through the V3 dry-run/staging backend core."""

    # Provider routes that are only allowed for testing
    TEST_PROVIDER_ROUTES = ("stub://", "shadow-heuristic://", "test://")

    def __init__(
        self,
        queue_store: QueueStore,
        *,
        fetcher: Fetcher | None = None,
        llm_provider: LLMProvider | None = None,
        prompt_registry: PromptRegistry | None = None,
        staging_root: Path | None = None,
        reject_threshold: float = 0.3,
        score_gate_enabled: bool = True,
        live_output: OutputPort | None = None,
        allow_test_provider: bool = False,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.queue_store = queue_store
        self.fetcher = fetcher or FixtureFetcher()
        self.llm_provider = llm_provider or StubLLMProvider()
        self.prompt_registry = prompt_registry or PromptRegistry.default(project_root)
        self.staging_root = Path(staging_root or queue_store.db_path.parent / "staging")
        self.reject_threshold = reject_threshold
        self.score_gate_enabled = score_gate_enabled
        self._live_output = live_output
        self.allow_test_provider = allow_test_provider
        self.dry_run_output = DryRunOutputPort()
        self.staging_output = StagingOutputPort(self.staging_root)

    def process_url(
        self,
        url: str,
        *,
        source: str = "manual",
        queue_task_id: int | None = None,
        mode: RuntimeMode = RuntimeMode.DRY_RUN,
        prompt_bundle: str | None = None,
        run_parallel_tests: bool = False,
        claim_task: bool = True,
    ) -> ProcessResult:
        mode = RuntimeMode(mode)
        active_bundle = prompt_bundle or self.prompt_registry.active_bundle_name
        stage_results: list[StageResult] = []
        parallel_results: list[PromptRunResult] = []

        # Guard against using test providers with real URLs
        provider_route = str(getattr(self.llm_provider, "model_route", ""))
        if not self.allow_test_provider and not url.startswith("fixture://"):
            if any(provider_route.startswith(route) for route in self.TEST_PROVIDER_ROUTES):
                error = TypedError(
                    failure_kind=FailureKind.RUNTIME_GUARD,
                    message=f"Test provider ({provider_route}) not allowed for non-fixture URL",
                    stage="runtime_guard",
                    retryable=False,
                    next_action=NextAction.MANUAL_REVIEW,
                    detail=f"URL: {url[:100]}, Provider: {provider_route}",
                )
                _append_stage(stage_results, "runtime_guard", error=error)
                if queue_task_id is not None:
                    self.queue_store.mark_failed_terminal(
                        queue_task_id,
                        failure_kind=FailureKind.RUNTIME_GUARD,
                        last_error=error.message,
                        detail=error.detail,
                        next_action=NextAction.MANUAL_REVIEW,
                    )
                return ProcessResult(
                    url=url,
                    source=source,
                    queue_task_id=queue_task_id,
                    current_stage="runtime_guard",
                    final_status=QueueStatus.FAILED_TERMINAL,
                    retryable=False,
                    failure_kind=error.failure_kind,
                    next_action=error.next_action,
                    output_path="",
                    telegram_status="",
                    prompt_bundle=active_bundle,
                    stage_results=stage_results,
                    error=error,
                )

        if mode is RuntimeMode.LIVE and self._live_output is None:
            error = TypedError(
                failure_kind=FailureKind.RUNTIME_GUARD,
                message="LIVE mode requires a live_output port",
                stage="runtime_mode",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
            )
            _append_stage(stage_results, "runtime_mode", error=error)
            return ProcessResult(
                url=url,
                source=source,
                queue_task_id=queue_task_id,
                current_stage="runtime_mode",
                final_status=QueueStatus.FAILED_TERMINAL,
                retryable=False,
                failure_kind=error.failure_kind,
                next_action=error.next_action,
                output_path="",
                telegram_status="",
                prompt_bundle=active_bundle,
                stage_results=stage_results,
                error=error,
            )

        task = self._resolve_task(url, source=source, queue_task_id=queue_task_id)
        task_url = task.url
        if claim_task:
            self.queue_store.mark_processing(task.id)
        _append_stage(stage_results, "queue_processing", detail={"task_id": task.id, "claimed": claim_task})

        fetched = _run_stage(stage_results, "fetch", lambda: self.fetcher.fetch(task_url))
        if isinstance(fetched, TypedError):
            return self._fail(
                task,
                fetched,
                source=source,
                prompt_bundle=active_bundle,
                current_stage="fetch",
                stage_results=stage_results,
                parallel_results=parallel_results,
            )
        fetched = _with_queue_reply_metadata(fetched, task)

        validation = _run_stage(stage_results, "validate", lambda: _validate_content(fetched))
        if isinstance(validation, TypedError):
            return self._fail(
                task,
                validation,
                source=source,
                prompt_bundle=active_bundle,
                current_stage="validate",
                stage_results=stage_results,
                parallel_results=parallel_results,
            )

        prompts = _run_stage(
            stage_results,
            "resolve_prompt_bundle",
            lambda: self._load_prompts(active_bundle),
        )
        if isinstance(prompts, TypedError):
            return self._fail(
                task,
                prompts,
                source=source,
                prompt_bundle=active_bundle,
                current_stage="resolve_prompt_bundle",
                stage_results=stage_results,
                parallel_results=parallel_results,
            )
        scoring_prompt, extraction_prompt, telegram_prompt, prompt_hash = prompts

        score_result = self._score_and_parse(fetched, active_bundle, prompt_hash, scoring_prompt, stage_results)
        if isinstance(score_result, TypedError):
            return self._fail(
                task,
                score_result,
                source=source,
                prompt_bundle=active_bundle,
                current_stage=score_result.stage,
                stage_results=stage_results,
                parallel_results=parallel_results,
            )

        score_rejected = _should_reject(score_result, self.reject_threshold)
        if score_rejected and self.score_gate_enabled:
            error = TypedError(
                failure_kind=FailureKind.VALIDATION_FAILED,
                message="Scoring rejected content",
                stage="score_gate",
                retryable=False,
                next_action=NextAction.DROP,
                detail=f"final_score={score_result.final_score:g}, signal_tier={score_result.signal_tier}",
            )
            _append_stage(stage_results, "score_gate", error=error)
            rejected = self.queue_store.mark_rejected(
                task.id,
                reason=error.message,
                detail=error.detail,
                failure_kind=error.failure_kind,
            )
            return self._result_from_task(
                rejected,
                source=source,
                prompt_bundle=active_bundle,
                current_stage="score_gate",
                stage_results=stage_results,
                score_result=score_result,
                parallel_results=parallel_results,
                error=error,
            )
        _append_stage(stage_results, "score_gate", detail={
            "accepted": True,
            "gate_enabled": self.score_gate_enabled,
            "forced_extract": score_rejected and not self.score_gate_enabled,
            "final_score": score_result.final_score,
            "signal_tier": score_result.signal_tier,
        })

        extraction_result = self._extract_and_parse(
            fetched,
            score_result,
            active_bundle,
            prompt_hash,
            extraction_prompt,
            stage_results,
        )
        if isinstance(extraction_result, TypedError):
            return self._fail(
                task,
                extraction_result,
                source=source,
                prompt_bundle=active_bundle,
                current_stage=extraction_result.stage,
                stage_results=stage_results,
                score_result=score_result,
                parallel_results=parallel_results,
            )

        if run_parallel_tests:
            parallel_results = _run_stage(
                stage_results,
                "parallel_bundles",
                lambda: self._run_parallel_bundles(fetched, active_bundle),
            )
            if isinstance(parallel_results, TypedError):
                parallel_results = [
                    PromptRunResult(
                        prompt_bundle="parallel_bundles",
                        prompt_hash="",
                        ok=False,
                        error=parallel_results,
                    )
                ]

        telegram_text = _run_stage(
            stage_results,
            "telegram_format",
            lambda: self.llm_provider.format_telegram(
                score_result,
                extraction_result,
                telegram_prompt,
                content=fetched,
            ),
        )
        if isinstance(telegram_text, TypedError):
            return self._fail(
                task,
                telegram_text,
                source=source,
                prompt_bundle=active_bundle,
                current_stage="telegram_format",
                stage_results=stage_results,
                score_result=score_result,
                extraction_result=extraction_result,
                parallel_results=parallel_results,
            )

        # Compute observability fields for output
        _provider_route = str(getattr(self.llm_provider, "model_route", ""))
        _is_test_provider = any(_provider_route.startswith(r) for r in self.TEST_PROVIDER_ROUTES)
        _runtime_fingerprint = str(getattr(self.queue_store, "runtime_fingerprint", ""))[:64]

        output = _run_stage(
            stage_results,
            "output",
            lambda: self._output_port(mode).write(
                fetched,
                score_result,
                extraction_result,
                telegram_text,
                prompt_bundle=active_bundle,
                prompt_hash=prompt_hash,
                task_id=task.id,
                runtime_mode=mode.value,
                provider_route=_provider_route,
                is_test_provider=_is_test_provider,
                runtime_fingerprint=_runtime_fingerprint,
            ),
            error_selector=lambda result: result.error if not result.ok else None,
        )
        if not output.ok:
            error = output.error or TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Output failed without detail",
                stage="output",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
            )
            return self._fail(
                task,
                error,
                source=source,
                prompt_bundle=active_bundle,
                current_stage="output",
                stage_results=stage_results,
                score_result=score_result,
                extraction_result=extraction_result,
                parallel_results=parallel_results,
            )

        done = self.queue_store.mark_done(
            task.id,
            result_title=extraction_result.title,
            output_path=output.obsidian_path,
        )
        return self._result_from_task(
            done,
            source=source,
            prompt_bundle=active_bundle,
            current_stage="done",
            stage_results=stage_results,
            score_result=score_result,
            extraction_result=extraction_result,
            parallel_results=parallel_results,
            telegram_status=output.telegram_status,
        )

    def _resolve_task(self, url: str, *, source: str, queue_task_id: int | None) -> QueueTask:
        if queue_task_id is not None:
            return self.queue_store.get_task(queue_task_id)
        return self.queue_store.enqueue(url, source=source)

    def _load_prompts(self, bundle_name: str) -> tuple[str, str, str, str] | TypedError:
        try:
            scoring_prompt = self.prompt_registry.load_prompt(bundle_name, "scoring")
            extraction_prompt = self.prompt_registry.load_prompt(bundle_name, "extraction")
            telegram_prompt = self.prompt_registry.load_prompt(bundle_name, "telegram_brief")
        except Exception as exc:
            return TypedError(
                failure_kind=FailureKind.UNKNOWN,
                message=f"Prompt bundle could not be loaded: {bundle_name}",
                stage="resolve_prompt_bundle",
                retryable=False,
                next_action=NextAction.INVESTIGATE,
                detail=str(exc),
            )
        prompt_hash = sha256_text(scoring_prompt + extraction_prompt + telegram_prompt, length=16)
        return scoring_prompt, extraction_prompt, telegram_prompt, prompt_hash

    def _score_and_parse(
        self,
        content: FetchedContent,
        bundle_name: str,
        prompt_hash: str,
        scoring_prompt: str,
        stage_results: list[StageResult],
    ) -> ScoreResult | TypedError:
        # 对长内容进行预处理
        processed_content = self._maybe_preprocess_content(content, stage_results)
        if isinstance(processed_content, TypedError):
            return processed_content

        raw_score = _run_stage(stage_results, "score", lambda: self.llm_provider.score(processed_content, scoring_prompt))
        if isinstance(raw_score, TypedError):
            return raw_score
        return _run_stage(
            stage_results,
            "score_parse",
            lambda: parse_score_result(
                raw_score,
                prompt_bundle=bundle_name,
                prompt_hash=prompt_hash,
                model_route=getattr(self.llm_provider, "model_route", "stub://unknown"),
            ),
        )

    def _extract_and_parse(
        self,
        content: FetchedContent,
        score_result: ScoreResult,
        bundle_name: str,
        prompt_hash: str,
        extraction_prompt: str,
        stage_results: list[StageResult],
    ) -> ExtractionResult | TypedError:
        # 对长内容进行预处理
        processed_content = self._maybe_preprocess_content(content, stage_results)
        if isinstance(processed_content, TypedError):
            return processed_content

        raw_extraction = _run_stage(
            stage_results,
            "extract",
            lambda: self.llm_provider.extract(processed_content, score_result, extraction_prompt),
        )
        if isinstance(raw_extraction, TypedError):
            return raw_extraction
        return _run_stage(
            stage_results,
            "extraction_parse",
            lambda: parse_extraction_result(
                raw_extraction,
                prompt_bundle=bundle_name,
                prompt_hash=prompt_hash,
                model_route=getattr(self.llm_provider, "model_route", "stub://unknown"),
            ),
        )

    def _run_parallel_bundles(
        self,
        content: FetchedContent,
        active_bundle: str,
    ) -> list[PromptRunResult] | TypedError:
        results: list[PromptRunResult] = []
        for bundle in self.prompt_registry.bundles_for_parallel_test():
            if bundle.name == active_bundle:
                continue
            prompts = self._load_prompts(bundle.name)
            if isinstance(prompts, TypedError):
                results.append(
                    PromptRunResult(
                        prompt_bundle=bundle.name,
                        prompt_hash="",
                        ok=False,
                        error=prompts,
                    )
                )
                continue
            scoring_prompt, extraction_prompt, _telegram_prompt, prompt_hash = prompts
            raw_score = self.llm_provider.score(content, scoring_prompt)
            if isinstance(raw_score, TypedError):
                results.append(
                    PromptRunResult(
                        prompt_bundle=bundle.name,
                        prompt_hash=prompt_hash,
                        ok=False,
                        error=raw_score,
                    )
                )
                continue
            score_result = parse_score_result(
                raw_score,
                prompt_bundle=bundle.name,
                prompt_hash=prompt_hash,
                model_route=getattr(self.llm_provider, "model_route", "stub://unknown"),
            )
            if isinstance(score_result, TypedError):
                results.append(
                    PromptRunResult(
                        prompt_bundle=bundle.name,
                        prompt_hash=prompt_hash,
                        ok=False,
                        error=score_result,
                    )
                )
                continue
            raw_extraction = self.llm_provider.extract(content, score_result, extraction_prompt)
            if isinstance(raw_extraction, TypedError):
                results.append(
                    PromptRunResult(
                        prompt_bundle=bundle.name,
                        prompt_hash=prompt_hash,
                        ok=False,
                        score_result=score_result,
                        error=raw_extraction,
                    )
                )
                continue
            extraction_result = parse_extraction_result(
                raw_extraction,
                prompt_bundle=bundle.name,
                prompt_hash=prompt_hash,
                model_route=getattr(self.llm_provider, "model_route", "stub://unknown"),
            )
            if isinstance(extraction_result, TypedError):
                results.append(
                    PromptRunResult(
                        prompt_bundle=bundle.name,
                        prompt_hash=prompt_hash,
                        ok=False,
                        score_result=score_result,
                        error=extraction_result,
                    )
                )
                continue
            results.append(
                PromptRunResult(
                    prompt_bundle=bundle.name,
                    prompt_hash=prompt_hash,
                    ok=True,
                    score_result=score_result,
                    extraction_result=extraction_result,
                )
            )
        return results

    def _maybe_preprocess_content(
        self,
        content: FetchedContent,
        stage_results: list[StageResult],
    ) -> FetchedContent | TypedError:
        """
        对长内容进行预处理，避免 LLM 超时

        仅对超过长度阈值的内容进行压缩，并在 stage_results 中记录
        """
        # 长度阈值：10000 字符
        LONG_CONTENT_THRESHOLD = 10000

        if len(content.text) <= LONG_CONTENT_THRESHOLD:
            return content

        # 记录预处理阶段
        from dataclasses import replace
        import time

        start_time = time.time()

        try:
            # 使用预处理函数压缩内容
            compressed_text = _preprocess_long_content(content.text)
            compression_ratio = len(compressed_text) / len(content.text)

            # 创建新的 FetchedContent 对象
            processed_content = replace(content, text=compressed_text)

            # 记录预处理详情
            duration_ms = int((time.time() - start_time) * 1000)
            stage_results.append(
                StageResult(
                    stage="preprocess",
                    ok=True,
                    started_at=start_time,
                    ended_at=time.time(),
                    duration_ms=duration_ms,
                    error=None,
                    detail={
                        "original_length": len(content.text),
                        "compressed_length": len(compressed_text),
                        "compression_ratio": round(compression_ratio, 3),
                        "chars_saved": len(content.text) - len(compressed_text),
                    },
                )
            )

            return processed_content

        except Exception as exc:
            # 预处理失败，返回错误但允许使用原始内容重试
            error = TypedError(
                failure_kind=FailureKind.PARSE_ERROR,
                message=f"Content preprocessing failed: {exc}",
                stage="preprocess",
                retryable=True,  # 允许重试
                next_action=NextAction.RETRY_LATER,
                detail=str(exc),
            )

            duration_ms = int((time.time() - start_time) * 1000)
            stage_results.append(
                StageResult(
                    stage="preprocess",
                    ok=False,
                    started_at=start_time,
                    ended_at=time.time(),
                    duration_ms=duration_ms,
                    error=error,
                    detail={"error_message": str(exc)},
                )
            )

            return error

    def _output_port(self, mode: RuntimeMode) -> OutputPort:
        if mode is RuntimeMode.DRY_RUN:
            return self.dry_run_output
        if mode is RuntimeMode.STAGING:
            return self.staging_output
        if mode is RuntimeMode.LIVE:
            if self._live_output is None:
                raise ValueError("LIVE mode requires a live_output port")
            return self._live_output
        raise ValueError(f"Unsupported output mode: {mode.value}")

    def _fail(
        self,
        task: QueueTask,
        error: TypedError,
        *,
        source: str,
        prompt_bundle: str,
        current_stage: str,
        stage_results: list[StageResult],
        score_result: ScoreResult | None = None,
        extraction_result: ExtractionResult | None = None,
        parallel_results: list[PromptRunResult] | None = None,
    ) -> ProcessResult:
        if error.retryable:
            updated = self.queue_store.schedule_retry(
                task.id,
                failure_kind=error.failure_kind,
                last_error=error.message,
                detail=error.detail,
                next_retry_at=error.next_retry_at or retry_at(15),
                next_action=error.next_action,
            )
        elif error.failure_kind is FailureKind.VALIDATION_FAILED and error.next_action is NextAction.DROP:
            updated = self.queue_store.mark_rejected(
                task.id,
                reason=error.message,
                detail=error.detail,
                failure_kind=error.failure_kind,
            )
        else:
            updated = self.queue_store.mark_failed_terminal(
                task.id,
                failure_kind=error.failure_kind,
                last_error=error.message,
                detail=error.detail,
                next_action=error.next_action,
            )
        return self._result_from_task(
            updated,
            source=source,
            prompt_bundle=prompt_bundle,
            current_stage=current_stage,
            stage_results=stage_results,
            score_result=score_result,
            extraction_result=extraction_result,
            parallel_results=parallel_results or [],
            error=error,
        )

    @staticmethod
    def _result_from_task(
        task: QueueTask,
        *,
        source: str,
        prompt_bundle: str,
        current_stage: str,
        stage_results: list[StageResult],
        score_result: ScoreResult | None = None,
        extraction_result: ExtractionResult | None = None,
        parallel_results: list[PromptRunResult] | None = None,
        telegram_status: str = "",
        error: TypedError | None = None,
    ) -> ProcessResult:
        return ProcessResult(
            url=task.url,
            source=source,
            queue_task_id=task.id,
            current_stage=current_stage,
            final_status=task.status,
            retryable=task.status is QueueStatus.RETRY_SCHEDULED,
            failure_kind=task.failure_kind,
            next_action=task.next_action,
            output_path=task.output_path,
            telegram_status=telegram_status,
            prompt_bundle=prompt_bundle,
            stage_results=stage_results,
            score_result=score_result,
            extraction_result=extraction_result,
            parallel_results=parallel_results or [],
            error=error,
        )


def _validate_content(content: FetchedContent) -> bool | TypedError:
    if not content.text.strip():
        return TypedError(
            failure_kind=FailureKind.VALIDATION_FAILED,
            message="Fetched content is empty",
            stage="validate",
            retryable=False,
            next_action=NextAction.DROP,
            detail=content.url,
        )
    if _looks_content_blocked(content):
        return TypedError(
            failure_kind=FailureKind.CONTENT_BLOCKED,
            message="Fetched content appears to be a platform block/verification page",
            stage="validate",
            retryable=False,
            next_action=NextAction.MANUAL_REVIEW,
            detail=content.url,
        )
    return True


def _looks_content_blocked(content: FetchedContent) -> bool:
    text = content.text.strip()
    lowered = text.lower()
    blocked_markers = (
        "当前环境异常",
        "完成验证后即可继续访问",
        "去验证",
        "environment abnormal",
        "verify you are human",
        "please complete verification",
    )
    if any(marker in lowered or marker in text for marker in blocked_markers):
        return True
    return content.source_type == "wechat_article" and len(text) < 200


def _with_queue_reply_metadata(content: FetchedContent, task: QueueTask) -> FetchedContent:
    if not task.reply_channel and not task.reply_chat_id:
        return content
    metadata = dict(content.metadata)
    if task.reply_channel:
        metadata["reply_channel"] = task.reply_channel
    if task.reply_chat_id:
        metadata["reply_chat_id"] = task.reply_chat_id
    return replace(content, metadata=metadata)


def _should_reject(score: ScoreResult, threshold: float) -> bool:
    return score.signal_tier.lower() == "reject" or score.final_score < threshold


def _run_stage(
    stage_results: list[StageResult],
    stage: str,
    fn: Callable[[], T],
    *,
    error_selector: Callable[[T], TypedError | None] | None = None,
) -> T:
    started_at = utc_now()
    started = time.perf_counter()
    result = fn()
    ended_at = utc_now()
    error = result if isinstance(result, TypedError) else None
    if error is None and error_selector is not None:
        error = error_selector(result)
    stage_results.append(
        StageResult(
            stage=stage,
            ok=error is None,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=error,
        )
    )
    return result


def _append_stage(
    stage_results: list[StageResult],
    stage: str,
    *,
    error: TypedError | None = None,
    detail: dict[str, object] | None = None,
) -> None:
    now = utc_now()
    stage_results.append(
        StageResult(
            stage=stage,
            ok=error is None,
            started_at=now,
            ended_at=now,
            duration_ms=0,
            error=error,
            detail=detail or {},
        )
    )


def _preprocess_long_content(text: str, max_length: int = 8000) -> str:
    """
    预处理长文本内容，智能压缩到合适长度以避免 LLM 超时

    Args:
        text: 原始文本
        max_length: 最大长度限制

    Returns:
        压缩后的文本
    """
    import re

    if len(text) <= max_length:
        return text

    # 移除常见噪音内容
    noise_patterns = [
        r'点击.*?关注.*?',
        r'扫码.*?关注.*?',
        r'转载.*?授权.*?',
        r'本文.*?版权.*?',
        r'更多精彩.*?关注.*?',
        r'欢迎.*?订阅.*?',
    ]

    cleaned_text = text
    for pattern in noise_patterns:
        cleaned_text = re.sub(pattern, '', cleaned_text, flags=re.IGNORECASE)

    # 分段处理
    paragraphs = cleaned_text.split('\n')
    high_quality_paragraphs = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 跳过噪音段落
        skip_words = ['点击关注', '扫码关注', '转载请注明', '版权声明',
                     '商务合作', '投稿', '广告', '更多精彩', '推荐阅读']
        if any(skip_word in para.lower() for skip_word in skip_words):
            continue

        # 保留有实质内容的段落
        if len(para) >= 20:
            high_quality_paragraphs.append(para)

    if not high_quality_paragraphs:
        return text[:max_length]

    # 关键词列表
    key_phrases = [
        '融资', '投资', '估值', '上市', 'IPO', '并购',
        '技术', '研发', '创新', '发布', '推出',
        '数据', '增长', '下降', '营收', '利润',
        '认为', '指出', '强调', '透露', '宣布',
    ]

    # 段落评分
    scored_paragraphs = []
    for i, para in enumerate(high_quality_paragraphs):
        score = 0

        # 开头和结尾段落加分
        if i < 3:
            score += 3
        elif i >= len(high_quality_paragraphs) - 3:
            score += 3

        # 包含关键词加分
        para_lower = para.lower()
        for phrase in key_phrases:
            if phrase in para_lower:
                score += 2

        # 段落长度适中加分
        if 50 <= len(para) <= 300:
            score += 1

        scored_paragraphs.append((score, i, para))

    # 选择高分段落（去重）
    seen_paras = set()
    unique_paragraphs = []
    for para in high_quality_paragraphs:
        if para not in seen_paras:
            seen_paras.add(para)
            unique_paragraphs.append(para)

    # 重新评分去重后的段落
    scored_paragraphs = []
    for i, para in enumerate(unique_paragraphs):
        score = 0

        # 开头和结尾段落加分
        if i < 3:
            score += 3
        elif i >= len(unique_paragraphs) - 3:
            score += 3

        # 包含关键词加分
        para_lower = para.lower()
        for phrase in key_phrases:
            if phrase in para_lower:
                score += 2

        # 段落长度适中加分
        if 50 <= len(para) <= 300:
            score += 1

        scored_paragraphs.append((score, i, para))

    # 按分数排序选择前15个
    scored_paragraphs.sort(reverse=True)
    selected_paragraphs = [
        para for score, i, para in scored_paragraphs[:15]
    ]

    # 组合并限制长度
    compressed_text = '\n\n'.join(selected_paragraphs)

    if len(compressed_text) > max_length:
        compressed_text = compressed_text[:max_length]
        last_period = compressed_text.rfind('。')
        if last_period > max_length * 0.8:
            compressed_text = compressed_text[:last_period + 1]

    return compressed_text.strip()
