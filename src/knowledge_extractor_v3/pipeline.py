"""V4 pipeline: one URL in, one absorption call, one routed output.

Flow: fetch -> validate -> absorb (single LLM call: three dimensions + card
material) -> code-weighted score/tier -> route -> deterministic card render ->
output (Obsidian archive + WeChat outbox lanes). Scoring and extraction were
merged into one call for latency and cost; the card is rendered in code, so a
model can never break the delivery format.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Callable, TypeVar

from .absorption_prompt import (
    PROMPT_BUNDLE,
    AbsorptionPrompt,
    AbsorptionPromptError,
    load_absorption_prompt,
)
from .brief_contract import validate_brief
from .brief_renderer import render_wechat_card
from .fetchers.base import Fetcher
from .fetchers.fixture import FixtureFetcher
from .llm.provider import LLMProvider, StubLLMProvider
from .models import (
    ExtractionResult,
    FetchedContent,
    ProcessResult,
    RuntimeMode,
    ScoreResult,
    StageResult,
    TypedError,
    retry_at,
    utc_now,
)
from .outputs.obsidian import DryRunOutputPort, OutputPort, StagingOutputPort
from .prompt_parser import parse_absorption_result
from .queue_store import FailureKind, NextAction, QueueStatus, QueueStore, QueueTask
from .routing import Route, SourcePreference, route_from_score


T = TypeVar("T")


class Pipeline:
    """Run one queue task through the V4 absorption core."""

    # Provider routes that are only allowed for testing
    TEST_PROVIDER_ROUTES = ("stub://", "shadow-heuristic://", "test://")

    def __init__(
        self,
        queue_store: QueueStore,
        *,
        fetcher: Fetcher | None = None,
        llm_provider: LLMProvider | None = None,
        staging_root: Path | None = None,
        live_output: OutputPort | None = None,
        allow_test_provider: bool = False,
        source_preferences: Mapping[str, SourcePreference] | None = None,
        absorption_prompt: AbsorptionPrompt | None = None,
        vault_dedup=None,
        story_dedup=None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[2]
        self.queue_store = queue_store
        self.fetcher = fetcher or FixtureFetcher()
        self.llm_provider = llm_provider or StubLLMProvider()
        try:
            self.absorption_prompt = absorption_prompt or load_absorption_prompt(project_root)
        except AbsorptionPromptError as exc:
            raise AbsorptionPromptError(str(exc)) from exc
        self.staging_root = Path(staging_root or queue_store.db_path.parent / "staging")
        self._source_preferences = dict(source_preferences or {})
        self._live_output = live_output
        self.allow_test_provider = allow_test_provider
        # LIVE-only vault dedup service (outputs.vault_index.VaultDedupService);
        # typed loosely to keep the pipeline free of an outputs import edge.
        self.vault_dedup = vault_dedup
        # LIVE-only story-identity dedup (outputs.story_identity.StoryDedupService);
        # same loose-typing rationale.
        self.story_dedup = story_dedup
        self.dry_run_output = DryRunOutputPort()
        self.staging_output = StagingOutputPort(self.staging_root)

    def process_url(
        self,
        url: str,
        *,
        source: str = "manual",
        queue_task_id: int | None = None,
        mode: RuntimeMode = RuntimeMode.DRY_RUN,
        claim_task: bool = True,
    ) -> ProcessResult:
        mode = RuntimeMode(mode)
        bundle = self.absorption_prompt.bundle
        stage_results: list[StageResult] = []

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
                    prompt_bundle=bundle,
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
                prompt_bundle=bundle,
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
                task, fetched, source=source, current_stage="fetch", stage_results=stage_results
            )
        fetched = _with_queue_reply_metadata(fetched, task)

        validation = _run_stage(stage_results, "validate", lambda: _validate_content(fetched))
        if isinstance(validation, TypedError):
            return self._fail(
                task, validation, source=source, current_stage="validate", stage_results=stage_results
            )

        # Vault-layer dedup early exit: an archived duplicate or a same-URL
        # update never reaches the absorption call, so reprocessed tasks and
        # source-page micro-changes cost at most one fetch.
        if self.vault_dedup is not None:
            lookup = _run_stage(
                stage_results,
                "dedup_check",
                lambda: self.vault_dedup.lookup(content_hash=fetched.content_hash, url=fetched.url),
            )
            if lookup.by_hash is not None and lookup.by_hash.path.exists():
                _append_stage(stage_results, "dedup_check", detail={
                    "outcome": "duplicate_hash",
                    "canonical": lookup.by_hash.path.name,
                })
                done = self.queue_store.mark_done(
                    task.id,
                    result_title=lookup.by_hash.title,
                    output_path=str(lookup.by_hash.path),
                )
                return self._result_from_task(
                    done,
                    source=source,
                    current_stage="dedup_check",
                    stage_results=stage_results,
                    dedup_outcome="duplicate_hash",
                )
            if lookup.by_url is not None and lookup.by_url.path.exists():
                outcome = _run_stage(
                    stage_results,
                    "increment",
                    lambda: self.vault_dedup.merge_update(lookup.by_url, fetched),
                )
                if isinstance(outcome, TypedError):
                    return self._fail(
                        task, outcome, source=source, current_stage="increment", stage_results=stage_results
                    )
                done = self.queue_store.mark_done(
                    task.id,
                    result_title=lookup.by_url.title,
                    output_path=outcome.path,
                )
                return self._result_from_task(
                    done,
                    source=source,
                    current_stage="increment",
                    stage_results=stage_results,
                    dedup_outcome={
                        "duplicate": "duplicate_similar",
                        "merged": "merged_update",
                        "no_update": "no_update",
                    }[outcome.kind],
                )

        absorbed = self._absorb_and_parse(fetched, stage_results)
        if isinstance(absorbed, TypedError):
            return self._fail(
                task,
                absorbed,
                source=source,
                current_stage=absorbed.stage,
                stage_results=stage_results,
            )
        score_result, extraction_result = absorbed

        # Routing: the score prioritises, spam/<4.0 drops. The preference
        # override rescues favoured channels; the content-length floor keeps
        # fetch skeletons out of the outbox.
        route_decision = route_from_score(
            score_result,
            source=task.source,
            url=task_url,
            content_chars=len(fetched.text),
            source_preferences=self._source_preferences,
        )
        _append_stage(stage_results, "routing", detail={
            "route": route_decision.route.value,
            "final_score": score_result.final_score,
            "information_gain": score_result.information_gain,
            "action_value": score_result.action_value,
            "relevance": score_result.relevance,
            "is_spam": score_result.is_spam,
            "reason": route_decision.reason,
        })

        if route_decision.route is Route.REJECT:
            error = TypedError(
                failure_kind=FailureKind.VALIDATION_FAILED,
                message="Content rejected by routing (reject)",
                stage="routing",
                retryable=False,
                next_action=NextAction.DROP,
                detail=route_decision.reason,
            )
            rejected = self.queue_store.mark_rejected(
                task.id,
                reason=error.message,
                detail=error.detail,
                failure_kind=error.failure_kind,
            )
            return self._result_from_task(
                rejected,
                source=source,
                current_stage="routing",
                stage_results=stage_results,
                score_result=score_result,
                error=error,
            )

        # Story-identity gate (post-absorption, pre-write): the same editorial
        # artifact arriving through another transport — an aggregator digest of
        # the article we already archived, a tracking-parameter mirror — must
        # not fork into a second article file. The absorption card is the
        # canonical editorial representation, so identity is decided on it.
        # Cost: the absorption call is already spent; the vault stays
        # one-article-per-story and the task completes at the canonical path.
        if self.story_dedup is not None:
            story_match = self.story_dedup.check(
                url=task_url,
                title=extraction_result.title,
                brief_markdown=extraction_result.obsidian_brief_markdown,
            )
            if story_match is not None:
                _append_stage(stage_results, "story_dedup", detail={
                    "outcome": "duplicate_story",
                    "canonical": story_match.canonical.path.name,
                    "shared_rare": list(story_match.shared_rare[:6]),
                    "containment": round(story_match.containment, 3),
                    "jaccard": round(story_match.jaccard, 3),
                    "title_jaccard": round(story_match.title_jaccard, 3),
                })
                try:
                    self.story_dedup.record_suppression(
                        story_match,
                        url=task_url,
                        task_id=task.id,
                        final_score=score_result.final_score,
                    )
                except Exception:
                    pass  # audit trail is best-effort; the dedup decision stands
                done = self.queue_store.mark_done(
                    task.id,
                    result_title=story_match.canonical.title,
                    output_path=str(story_match.canonical.path),
                )
                return self._result_from_task(
                    done,
                    source=source,
                    current_stage="story_dedup",
                    stage_results=stage_results,
                    score_result=score_result,
                    extraction_result=extraction_result,
                    dedup_outcome="duplicate_story",
                )

        # Deterministic card render against the ORIGINAL fetched text so the
        # verbatim-quote gate cannot be fooled by preprocessing truncation.
        telegram_text = _run_stage(
            stage_results,
            "card_render",
            lambda: render_wechat_card(score_result, extraction_result, fetched),
        )

        # Compute observability fields for output
        _provider_route = str(getattr(self.llm_provider, "model_route", ""))
        _is_test_provider = any(_provider_route.startswith(r) for r in self.TEST_PROVIDER_ROUTES)
        _runtime_fingerprint = str(getattr(self.queue_store, "runtime_fingerprint", ""))[:64]

        # Only push routes reach the WeChat outbox. archive_only keeps Obsidian
        # but stays out of the user's inbox; the lane tells the consumer which
        # digest schedule (morning/evening vs weekly) to attach it to.
        _wechat_lane = {
            Route.BUSINESS_PUSH: "business",
            Route.STRATEGIC_DIGEST: "strategic",
        }.get(route_decision.route)

        # Safety net on the deterministic card. The renderer enforces the quote
        # rule itself (omitting the section on mismatch), so a violation here
        # means a renderer bug — record it rather than silently trusting it.
        _brief_contract_failed = False
        if _wechat_lane:
            brief_errors = validate_brief(telegram_text)
            if brief_errors:
                _brief_contract_failed = True
                _wechat_lane = None
                contract_error = TypedError(
                    failure_kind=FailureKind.VALIDATION_FAILED,
                    message="WeChat brief failed the hard contract; withheld from outbox",
                    stage="brief_contract",
                    retryable=False,
                    next_action=NextAction.NONE,
                    detail="; ".join(brief_errors[:3]),
                )
                _append_stage(
                    stage_results,
                    "brief_contract",
                    error=contract_error,
                    detail={"brief_contract_failed": True, "errors": brief_errors},
                )
            else:
                _append_stage(stage_results, "brief_contract", detail={"passed": True})

        output = _run_stage(
            stage_results,
            "output",
            lambda: self._output_port(mode).write(
                fetched,
                score_result,
                extraction_result,
                telegram_text,
                prompt_bundle=bundle,
                prompt_hash=self.absorption_prompt.prompt_hash,
                task_id=task.id,
                runtime_mode=mode.value,
                provider_route=_provider_route,
                is_test_provider=_is_test_provider,
                runtime_fingerprint=_runtime_fingerprint,
                wechat_lane=_wechat_lane,
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
                current_stage="output",
                stage_results=stage_results,
                score_result=score_result,
                extraction_result=extraction_result,
            )

        done = self.queue_store.mark_done(
            task.id,
            result_title=extraction_result.title,
            output_path=output.obsidian_path,
        )
        return self._result_from_task(
            done,
            source=source,
            current_stage="done",
            stage_results=stage_results,
            score_result=score_result,
            extraction_result=extraction_result,
            telegram_status=output.telegram_status,
            wechat_status=output.wechat_status,
            route=route_decision.route.value,
            brief_contract_failed=_brief_contract_failed,
        )

    def _resolve_task(self, url: str, *, source: str, queue_task_id: int | None) -> QueueTask:
        if queue_task_id is not None:
            return self.queue_store.get_task(queue_task_id)
        return self.queue_store.enqueue(url, source=source)

    def _absorb_and_parse(
        self,
        content: FetchedContent,
        stage_results: list[StageResult],
    ) -> tuple[ScoreResult, ExtractionResult] | TypedError:
        # Pre-truncate long content once; the renderer later checks quotes
        # against the original text, not this preprocessed variant.
        processed_content = self._maybe_preprocess_content(content, stage_results)
        if isinstance(processed_content, TypedError):
            return processed_content

        raw = _run_stage(
            stage_results,
            "absorb",
            lambda: self.llm_provider.score(processed_content, self.absorption_prompt.text),
        )
        if isinstance(raw, TypedError):
            return raw
        return _run_stage(
            stage_results,
            "absorb_parse",
            lambda: parse_absorption_result(
                raw,
                prompt_bundle=self.absorption_prompt.bundle,
                prompt_hash=self.absorption_prompt.prompt_hash,
                model_route=getattr(self.llm_provider, "model_route", "stub://unknown"),
            ),
        )

    def _maybe_preprocess_content(
        self,
        content: FetchedContent,
        stage_results: list[StageResult],
    ) -> FetchedContent | TypedError:
        """Compress very long content to keep the single LLM call fast."""
        LONG_CONTENT_THRESHOLD = 10000

        if len(content.text) <= LONG_CONTENT_THRESHOLD:
            return content

        start_time = time.time()

        try:
            compressed_text = _preprocess_long_content(content.text)
            compression_ratio = len(compressed_text) / len(content.text)
            processed_content = replace(content, text=compressed_text)
            stage_results.append(
                StageResult(
                    stage="preprocess",
                    ok=True,
                    started_at=utc_now(),
                    ended_at=utc_now(),
                    duration_ms=int((time.time() - start_time) * 1000),
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
            error = TypedError(
                failure_kind=FailureKind.PARSE_ERROR,
                message=f"Content preprocessing failed: {exc}",
                stage="preprocess",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                detail=str(exc),
            )
            stage_results.append(
                StageResult(
                    stage="preprocess",
                    ok=False,
                    started_at=utc_now(),
                    ended_at=utc_now(),
                    duration_ms=int((time.time() - start_time) * 1000),
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
        current_stage: str,
        stage_results: list[StageResult],
        score_result: ScoreResult | None = None,
        extraction_result: ExtractionResult | None = None,
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
            current_stage=current_stage,
            stage_results=stage_results,
            score_result=score_result,
            extraction_result=extraction_result,
            error=error,
        )

    @staticmethod
    def _result_from_task(
        task: QueueTask,
        *,
        source: str,
        current_stage: str,
        stage_results: list[StageResult],
        score_result: ScoreResult | None = None,
        extraction_result: ExtractionResult | None = None,
        telegram_status: str = "",
        wechat_status: str = "",
        route: str = "",
        brief_contract_failed: bool = False,
        dedup_outcome: str = "",
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
            prompt_bundle=PROMPT_BUNDLE,
            wechat_status=wechat_status,
            route=route,
            brief_contract_failed=brief_contract_failed,
            dedup_outcome=dedup_outcome,
            stage_results=stage_results,
            score_result=score_result,
            extraction_result=extraction_result,
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
    if _is_article_type(content.source_type) and _looks_like_shell_content(content):
        return TypedError(
            failure_kind=FailureKind.CONTENT_BLOCKED,
            message="Article content appears to be a paywall shell, title-only, or insufficient body text",
            stage="validate",
            retryable=False,
            next_action=NextAction.DROP,
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


_ARTICLE_TYPES = frozenset({"web_article", "rss_feed", "wechat_article"})

_SHELL_INDICATORS = (
    "javascript is required",
    "please enable javascript",
    "requires javascript",
    "please enable cookies",
    "enable cookies to continue",
)

_LOGIN_INDICATORS = (
    "sign in to continue reading",
    "log in to continue",
    "register to continue",
    "create an account to continue",
    "subscribe to continue reading",
    "to continue reading, please",
    "this article is exclusive to subscribers",
)


def _is_article_type(source_type: str) -> bool:
    return source_type in _ARTICLE_TYPES


def _looks_like_shell_content(content: FetchedContent) -> bool:
    text = content.text.strip()
    title = content.title.strip()
    lower = text.lower()
    word_count = len(text.split())

    if word_count < 80:
        return True

    if title and _is_text_mostly_title(text, title):
        return True

    if any(indicator in lower for indicator in _SHELL_INDICATORS):
        return True
    if any(indicator in lower for indicator in _LOGIN_INDICATORS):
        return True

    from .fetchers.web import PAYWALL_MARKERS
    if any(marker in lower for marker in PAYWALL_MARKERS) and word_count < 300:
        return True

    return False


def _is_text_mostly_title(text: str, title: str) -> bool:
    remainder = text.replace(title, "").strip()
    return len(remainder.split()) < 30


def _with_queue_reply_metadata(content: FetchedContent, task: QueueTask) -> FetchedContent:
    if not task.reply_channel and not task.reply_chat_id:
        return content
    metadata = dict(content.metadata)
    if task.reply_channel:
        metadata["reply_channel"] = task.reply_channel
    if task.reply_chat_id:
        metadata["reply_chat_id"] = task.reply_chat_id
    return replace(content, metadata=metadata)


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
    """Compress very long text so the single LLM call stays fast.

    Keeps the highest-signal paragraphs (open/close weighting plus
    business-signal keywords), strips boilerplate, caps total length.
    """
    import re

    if len(text) <= max_length:
        return text

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

    paragraphs = cleaned_text.split('\n')
    high_quality_paragraphs = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        skip_words = ['点击关注', '扫码关注', '转载请注明', '版权声明',
                     '商务合作', '投稿', '广告', '更多精彩', '推荐阅读']
        if any(skip_word in para.lower() for skip_word in skip_words):
            continue

        if len(para) >= 20:
            high_quality_paragraphs.append(para)

    if not high_quality_paragraphs:
        return text[:max_length]

    key_phrases = [
        '融资', '投资', '估值', '上市', 'IPO', '并购',
        '技术', '研发', '创新', '发布', '推出',
        '数据', '增长', '下降', '营收', '利润',
        '认为', '指出', '强调', '透露', '宣布',
    ]

    seen_paras = set()
    unique_paragraphs = []
    for para in high_quality_paragraphs:
        if para not in seen_paras:
            seen_paras.add(para)
            unique_paragraphs.append(para)

    scored_paragraphs = []
    for i, para in enumerate(unique_paragraphs):
        score = 0

        if i < 3:
            score += 3
        elif i >= len(unique_paragraphs) - 3:
            score += 3

        para_lower = para.lower()
        for phrase in key_phrases:
            if phrase in para_lower:
                score += 2

        if 50 <= len(para) <= 300:
            score += 1

        scored_paragraphs.append((score, i, para))

    scored_paragraphs.sort(reverse=True)
    selected_paragraphs = [
        para for score, i, para in scored_paragraphs[:15]
    ]

    compressed_text = '\n\n'.join(selected_paragraphs)

    if len(compressed_text) > max_length:
        compressed_text = compressed_text[:max_length]
        last_period = compressed_text.rfind('。')
        if last_period > max_length * 0.8:
            compressed_text = compressed_text[:last_period + 1]

    return compressed_text.strip()
