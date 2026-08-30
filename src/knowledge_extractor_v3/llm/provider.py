"""LLM provider contract and deterministic stub for the V4 absorption call."""

from __future__ import annotations

import json
from typing import Protocol

from ..models import FetchedContent, ScoreResult, TypedError, retry_at
from ..queue_store import FailureKind, NextAction


class LLMProvider(Protocol):
    def score(self, content: FetchedContent, prompt: str) -> str | TypedError:
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
        return _json(_absorption_payload(content, scenario=scenario))

    def complete(self, content: str, prompt: str, *, stage: str = "review") -> str | TypedError:
        """Free-form completion for non-absorption calls (increment, review).

        Deterministically reports "no update" so LIVE-style wiring can call
        complete() on the stub without attributes errors; tests that drive
        the increment path override this method the way they override score().
        """
        return _json({
            "has_update": False,
            "delta_summary": "",
            "new_points": [],
            "changed_facts": [],
        })


def _scenario(content: FetchedContent) -> str:
    value = content.metadata.get("fixture_scenario")
    if isinstance(value, str) and value:
        return value
    if content.url.startswith("fixture://"):
        return content.url.removeprefix("fixture://").strip("/")
    return "high_signal"


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


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
