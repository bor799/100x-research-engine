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
            return '{"information_gain": 0.8, "action_value": 0.8, "relevance": 0.8, "is_spam": false, "title": "broken json"'
        if _is_absorption_prompt(prompt):
            return _json(_absorption_payload(content, scenario=scenario))
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


def _is_absorption_prompt(prompt: str) -> bool:
    return "information_gain" in prompt


def _absorption_payload(content: FetchedContent, *, scenario: str) -> dict[str, object]:
    """V4 single-call payload: three dimensions plus card/archive material.

    Dimensions feed the code-owned weighted blend (prompt_parser): the default
    high-signal fixture must blend >= 0.75 (tier A / business push candidate),
    low_quality below 0.40 (reject). The quote is a verbatim slice of the
    fixture text so renderer quote-gating tests exercise a real hit.
    """
    high = scenario != "low_quality"  # spam keeps high dims to prove the override
    spam = scenario == "spam"
    title = content.title or "Fixture absorption article"
    quote = content.text.strip()[:40] if content.text.strip() else ""
    return {
        "information_gain": 0.82 if high else 0.20,
        "action_value": 0.80 if high else 0.20,
        "relevance": 0.78 if high else 0.25,
        "is_spam": spam,
        "rationale": "Fixture absorption payload for V4 pipeline verification.",
        "title": title,
        "one_line_summary": f"{title} carries reusable signal for the reader.",
        "category": "技术创业",
        "experiences": ["Ship the smallest paid slice first; cut every non-core feature."] if high else [],
        "signals": ["Two-person teams reaching 200万 ARR are becoming routine."] if high else ["Nothing new here."],
        "key_facts": ["Fixture fact with a number: 3 人做到 200 万 ARR。"] if high else ["No decision-relevant facts."],
        "quote": quote,
        "next_action": "Verify the claim against one more source." if high else "",
        "obsidian_brief_markdown": (
            f"{title}\n\n{title} carries reusable signal for the reader.\n\n---\n\n"
            "## 经验\n- **核心点**: 先卖最小付费切片。\n\n"
            "## 关键事实\n- 3 人做到 200 万 ARR。\n\n"
            "## 信号\n- 小团队高 ARR 越来越常见。\n\n"
            + (f"## 金句\n\n> {quote}\n" if quote else "")
        ),
    }


def _score_payload(
    content: FetchedContent,
    *,
    score: float,
    final_score: float,
    signal_tier: str,
) -> dict[str, object]:
    high = signal_tier != "Reject"
    # Dimensions feed the code-owned linear blend (prompt_parser): high-signal
    # fixtures must blend above 0.8, low-quality below 0.3, matching the
    # scenario semantics the stub provider promises.
    return {
        "score": score,
        "final_score": final_score,
        "signal_tier": signal_tier,
        "L1": 0.82 if high else 0.25,
        "L2": 0.80 if high else 0.25,
        "L3": 0.82 if high else 0.25,
        "L4": 0.80 if high else 0.25,
        "objective_quality": 0.55 if high else 0.10,
        "actor_scene": 0.85 if high else 0.20,
        "operating_detail": 0.80 if high else 0.20,
        "causal_arc": 0.78 if high else 0.20,
        "transferability": 0.75 if high else 0.20,
        "evidence_strength": 0.72 if high else 0.20,
        "decision_window_status": "open" if high else "closed",
        "source_type": content.source_type,
        "source_tier": "primary" if high else "unverified",
        "interest_flag": "Independent" if high else "Unknown",
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
