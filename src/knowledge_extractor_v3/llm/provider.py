"""LLM provider contract and deterministic Phase 2 stub."""

from __future__ import annotations

import json
from typing import Protocol

from ..models import FetchedContent, ExtractionResult, ScoreResult, TypedError, retry_at
from ..queue_store import FailureKind, NextAction


class LLMProvider(Protocol):
    def score(self, content: FetchedContent, prompt: str) -> str | TypedError:
        ...

    def extract(self, content: FetchedContent, score: ScoreResult, prompt: str) -> str | TypedError:
        ...

    def format_telegram(
        self,
        score: ScoreResult,
        extraction: ExtractionResult,
        prompt: str,
        *,
        content: FetchedContent | None = None,
    ) -> str | TypedError:
        ...


class StubLLMProvider:
    """A no-network provider driven by fixture scenario metadata."""

    model_route = "stub://phase2"

    def score(self, content: FetchedContent, prompt: str) -> str | TypedError:
        scenario = _scenario(content)
        if scenario == "llm_rate_limit":
            return TypedError(
                failure_kind=FailureKind.LLM_RATE_LIMIT,
                message="Stub LLM rate limit",
                stage="score",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                next_retry_at=retry_at(15),
            )
        if scenario == "llm_timeout":
            return TypedError(
                failure_kind=FailureKind.LLM_TIMEOUT,
                message="Stub LLM timeout",
                stage="score",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                next_retry_at=retry_at(10),
            )
        if scenario == "parse_error":
            return '{"score": 8, "final_score": 0.84, "signal_tier": "A"'
        if scenario == "low_quality":
            return _json(_score_payload(content, score=2.0, final_score=0.18, signal_tier="Reject"))
        return _json(_score_payload(content, score=8.6, final_score=0.86, signal_tier="A"))

    def extract(self, content: FetchedContent, score: ScoreResult, prompt: str) -> str | TypedError:
        if _scenario(content) == "llm_timeout_extract":
            return TypedError(
                failure_kind=FailureKind.LLM_TIMEOUT,
                message="Stub LLM timeout during extraction",
                stage="extract",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                next_retry_at=retry_at(10),
            )
        return _json(_extraction_payload(content, score))

    def format_telegram(
        self,
        score: ScoreResult,
        extraction: ExtractionResult,
        prompt: str,
        *,
        content: FetchedContent | None = None,
    ) -> str | TypedError:
        return (
            f"{extraction.title}\n"
            f"{extraction.one_line_signal}\n"
            f"Score: {score.score:g}/10 ({score.signal_tier})\n"
            f"{content.url if content is not None else extraction.parsed.get('url', '')}"
        ).strip()


def _scenario(content: FetchedContent) -> str:
    value = content.metadata.get("fixture_scenario")
    if isinstance(value, str) and value:
        return value
    if content.url.startswith("fixture://"):
        return content.url.removeprefix("fixture://").strip("/")
    return "high_signal"


def _score_payload(
    content: FetchedContent,
    *,
    score: float,
    final_score: float,
    signal_tier: str,
) -> dict[str, object]:
    return {
        "score": score,
        "final_score": final_score,
        "signal_tier": signal_tier,
        "L1": "market timing",
        "L2": "distribution insight",
        "L3": "founder evidence",
        "L4": "actionable monitoring",
        "objective_quality": "high" if signal_tier != "Reject" else "low",
        "decision_window_status": "open" if signal_tier != "Reject" else "closed",
        "source_type": content.source_type,
        "source_tier": "primary" if signal_tier != "Reject" else "unverified",
        "interest_flag": "track" if signal_tier != "Reject" else "drop",
        "attribution_chain": [content.source, content.url],
        "rationale": "Fixture scoring payload for Phase 2 pipeline verification.",
        "key_claims": [
            "Distribution shift is observable in the article.",
            "The opportunity can be monitored with public signals.",
        ],
        "watch_items": [
            "follow-on funding",
            "customer proof",
        ],
    }


def _extraction_payload(content: FetchedContent, score: ScoreResult) -> dict[str, object]:
    title = content.title or "Untitled fixture article"
    return {
        "title": title,
        "one_line_signal": f"{title} is worth tracking as a {score.signal_tier} signal.",
        "decision_window_status": score.decision_window_status,
        "source_type": score.source_type,
        "source_tier": score.source_tier,
        "interest_flag": score.interest_flag,
        "attribution_chain": score.attribution_chain,
        "why_it_matters": [
            "It connects a current market behavior to a concrete execution wedge.",
        ],
        "evidence": [
            "Fixture content supplied a product, customer, and timing cue.",
        ],
        "inferences": [
            "The strongest next step is to verify repeatability with another source.",
        ],
        "risks_and_conflicts": [
            "Fixture data is not live evidence.",
        ],
        "recommended_actions": [
            "Track the company/source for one more proof point.",
        ],
        "monitoring_triggers": [
            "New customer announcement",
            "Fresh hiring or financing event",
        ],
        "obsidian_brief_markdown": (
            f"# {title}\n\n"
            f"- Signal: {score.signal_tier}\n"
            f"- Score: {score.score:g}/10\n"
            f"- URL: {content.url}\n\n"
            f"{content.text[:500].strip()}"
        ),
        "url": content.url,
    }


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
