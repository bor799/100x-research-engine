"""Parse the V4 absorption payload into typed results."""

from __future__ import annotations

import json
from typing import Any

from .models import ExtractionResult, ScoreResult, TypedError
from .queue_store import FailureKind, NextAction

# Single LLM call for the whole pipeline: the model emits three content
# dimensions plus card/archive material, and EVERY aggregate (score,
# final_score, signal_tier) is computed here so a model cannot drift on
# arithmetic. Source-credibility scoring is intentionally absent — the operator
# curates sources by hand and trusts them, so credibility is not an evaluation
# target.
ABSORPTION_WEIGHTS = {"information_gain": 0.40, "action_value": 0.35, "relevance": 0.25}
# Tier thresholds on final_score (0-1); anything below C's floor rejects.
ABSORPTION_TIER_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("A", 0.75),
    ("B", 0.50),
    ("C", 0.40),
)
ABSORPTION_REQUIRED_FIELDS = (
    "information_gain",
    "action_value",
    "relevance",
    "is_spam",
    "rationale",
    "title",
    "one_line_summary",
    "category",
    "experiences",
    "signals",
    "key_facts",
    "quote",
    "next_action",
    "obsidian_brief_markdown",
)
# Fields where an empty string is a legitimate value, not a missing field.
ABSORPTION_EMPTY_OK_FIELDS = frozenset({"quote", "next_action"})
ABSORPTION_LIST_FIELDS = frozenset({"experiences", "signals", "key_facts"})


def parse_absorption_result(
    raw_text: str,
    *,
    prompt_bundle: str,
    prompt_hash: str,
    model_route: str,
) -> tuple[ScoreResult, ExtractionResult] | TypedError:
    """Parse the single-call V4 absorption payload.

    The schema is strict: a 14-field contract has no excuse for dropped or
    string-typed dimensions, and a silent 0.0 default would corrupt the
    weighted score.
    """
    parsed = _load_object(raw_text, stage="absorb_parse")
    if isinstance(parsed, TypedError):
        return parsed

    missing = [
        field
        for field in ABSORPTION_REQUIRED_FIELDS
        if parsed.get(field) is None
        or (parsed.get(field) == "" and field not in ABSORPTION_EMPTY_OK_FIELDS)
    ]
    if missing:
        return _parse_error(
            "Absorption JSON is missing required fields",
            stage="absorb_parse",
            detail=", ".join(missing),
        )

    for field in ABSORPTION_LIST_FIELDS:
        if not isinstance(parsed[field], list):
            return _parse_error(
                f"Absorption field {field!r} must be a list",
                stage="absorb_parse",
            )

    dimensions = {}
    for field in ABSORPTION_WEIGHTS:
        value = parsed[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return _parse_error(
                f"Absorption dimension {field!r} must be a number",
                stage="absorb_parse",
            )
        dimensions[field] = _clip01(float(value))

    is_spam = parsed["is_spam"]
    if not isinstance(is_spam, bool):
        return _parse_error(
            "Absorption field 'is_spam' must be a boolean",
            stage="absorb_parse",
        )

    blended = sum(
        ABSORPTION_WEIGHTS[field] * dimensions[field] for field in ABSORPTION_WEIGHTS
    )
    final_score = round(blended, 4)
    score = round(blended * 10, 2)

    signal_tier = "Reject"
    for tier, floor in ABSORPTION_TIER_THRESHOLDS:
        if final_score >= floor:
            signal_tier = tier
            break
    if is_spam:
        signal_tier = "Reject"

    score_result = ScoreResult(
        prompt_bundle=prompt_bundle,
        prompt_hash=prompt_hash,
        model_route=model_route,
        raw_text=raw_text,
        parsed=parsed,
        score=score,
        final_score=final_score,
        signal_tier=signal_tier,
        information_gain=dimensions["information_gain"],
        action_value=dimensions["action_value"],
        relevance=dimensions["relevance"],
        rationale=str(parsed["rationale"]),
        is_spam=is_spam,
        is_promotional=is_spam,
    )
    extraction_result = ExtractionResult(
        prompt_bundle=prompt_bundle,
        prompt_hash=prompt_hash,
        model_route=model_route,
        raw_text=raw_text,
        parsed=parsed,
        title=str(parsed["title"]),
        one_line_signal=str(parsed["one_line_summary"]),
        obsidian_brief_markdown=str(parsed["obsidian_brief_markdown"]),
    )
    return score_result, extraction_result


def strip_markdown_fence(raw_text: str) -> str:
    text = raw_text.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _load_object(raw_text: str, *, stage: str) -> dict[str, Any] | TypedError:
    text = strip_markdown_fence(raw_text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return _parse_error("LLM response is not valid JSON", stage=stage, detail=str(exc))

    if not isinstance(parsed, dict):
        return _parse_error("LLM response must be a JSON object", stage=stage)
    return parsed


def _parse_error(message: str, *, stage: str, detail: str = "") -> TypedError:
    return TypedError(
        failure_kind=FailureKind.PARSE_ERROR,
        message=message,
        stage=stage,
        retryable=False,
        next_action=NextAction.INVESTIGATE,
        detail=detail,
    )


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
