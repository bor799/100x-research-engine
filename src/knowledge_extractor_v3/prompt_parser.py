"""Parse stub/provider LLM JSON into typed Phase 2 results."""

from __future__ import annotations

import json
from typing import Any

from .models import ExtractionResult, ScoreResult, TypedError
from .queue_store import FailureKind, NextAction


SCORING_REQUIRED_FIELDS = (
    "score",
    "final_score",
    "signal_tier",
    "L1",
    "L2",
    "L3",
    "L4",
    "objective_quality",
    "decision_window_status",
    "source_type",
    "source_tier",
    "interest_flag",
    "attribution_chain",
    "rationale",
    "key_claims",
    "watch_items",
)

EXTRACTION_REQUIRED_FIELDS = (
    "title",
    "one_line_signal",
    "decision_window_status",
    "source_type",
    "source_tier",
    "interest_flag",
    "attribution_chain",
    "why_it_matters",
    "evidence",
    "inferences",
    "risks_and_conflicts",
    "recommended_actions",
    "monitoring_triggers",
    "obsidian_brief_markdown",
)


def parse_score_result(
    raw_text: str,
    *,
    prompt_bundle: str,
    prompt_hash: str,
    model_route: str,
) -> ScoreResult | TypedError:
    parsed = _load_object(raw_text, stage="score_parse")
    if isinstance(parsed, TypedError):
        return parsed

    missing = _missing_required(parsed, SCORING_REQUIRED_FIELDS)
    if missing:
        return _parse_error(
            "Scoring JSON is missing required fields",
            stage="score_parse",
            detail=", ".join(missing),
        )

    score = _number(parsed["score"], "score", minimum=0, maximum=10, stage="score_parse")
    if isinstance(score, TypedError):
        return score
    final_score = _number(
        parsed["final_score"],
        "final_score",
        minimum=0,
        maximum=1,
        stage="score_parse",
    )
    if isinstance(final_score, TypedError):
        return final_score

    return ScoreResult(
        prompt_bundle=prompt_bundle,
        prompt_hash=prompt_hash,
        model_route=model_route,
        raw_text=raw_text,
        parsed=parsed,
        score=score,
        final_score=final_score,
        signal_tier=str(parsed["signal_tier"]),
        decision_window_status=str(parsed["decision_window_status"]),
        source_type=str(parsed["source_type"]),
        source_tier=str(parsed["source_tier"]),
        interest_flag=str(parsed["interest_flag"]),
        attribution_chain=parsed["attribution_chain"],
    )


def parse_extraction_result(
    raw_text: str,
    *,
    prompt_bundle: str,
    prompt_hash: str,
    model_route: str,
) -> ExtractionResult | TypedError:
    parsed = _load_object(raw_text, stage="extraction_parse")
    if isinstance(parsed, TypedError):
        return parsed

    missing = _missing_required(parsed, EXTRACTION_REQUIRED_FIELDS)
    if missing:
        return _parse_error(
            "Extraction JSON is missing required fields",
            stage="extraction_parse",
            detail=", ".join(missing),
        )

    return ExtractionResult(
        prompt_bundle=prompt_bundle,
        prompt_hash=prompt_hash,
        model_route=model_route,
        raw_text=raw_text,
        parsed=parsed,
        title=str(parsed["title"]),
        one_line_signal=str(parsed["one_line_signal"]),
        obsidian_brief_markdown=str(parsed["obsidian_brief_markdown"]),
    )


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


def _missing_required(parsed: dict[str, Any], required_fields: tuple[str, ...]) -> list[str]:
    missing = []
    for field in required_fields:
        value = parsed.get(field)
        if value is None or value == "":
            missing.append(field)
    return missing


def _number(
    value: object,
    field: str,
    *,
    minimum: float,
    maximum: float,
    stage: str,
) -> float | TypedError:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _parse_error(f"{field} must be a number", stage=stage)

    numeric_value = float(value)
    if not minimum <= numeric_value <= maximum:
        return _parse_error(f"{field} must be between {minimum:g} and {maximum:g}", stage=stage)
    return numeric_value


def _parse_error(message: str, *, stage: str, detail: str = "") -> TypedError:
    return TypedError(
        failure_kind=FailureKind.PARSE_ERROR,
        message=message,
        stage=stage,
        retryable=False,
        next_action=NextAction.INVESTIGATE,
        detail=detail,
    )
