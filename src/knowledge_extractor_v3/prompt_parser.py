"""Parse stub/provider LLM JSON into typed Phase 2 results."""

from __future__ import annotations

import json
from typing import Any

from .models import ExtractionResult, ScoreResult, TypedError
from .prompt_contract import EXTRACTION_REQUIRED_FIELDS, SCORING_REQUIRED_FIELDS
from .queue_store import FailureKind, NextAction
from .routing import compute_business_story_fit

# --- V4 absorption schema ----------------------------------------------------
#
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

# Code-owned aggregation of the L1-L4 dimensions. The prompt describes the same
# formula, but arithmetic lives here so a model cannot silently crush good
# content by multiplying three sub-1.0 dimensions (the L1*L2*L3 product that
# zeroed out entire unfamiliar sources) or by drifting on the math itself.
OBJECTIVE_QUALITY_WEIGHTS = {"L1": 0.40, "L2": 0.30, "L3": 0.30}
FINAL_SCORE_QUALITY_WEIGHT = 0.70  # remainder goes to L4 user-affinity


def parse_absorption_result(
    raw_text: str,
    *,
    prompt_bundle: str,
    prompt_hash: str,
    model_route: str,
) -> tuple[ScoreResult, ExtractionResult] | TypedError:
    """Parse the single-call V4 absorption payload.

    Unlike the lenient legacy `_dim` handling, the 3-dimension schema is
    strict: a 14-field contract has no excuse for dropped or string-typed
    dimensions, and a silent 0.0 default would corrupt the weighted score.
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
        decision_window_status="unknown",
        source_type="",
        source_tier="",
        interest_flag="Independent",
        attribution_chain=None,
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

    # Parse the five business-story dimensions leniently. They are required of
    # the active bundle's prompt schema but the parser must not crash when a
    # legacy parallel bundle (or a model that dropped a field) omits them: a
    # missing dimension defaults to 0, which safely routes the item away from
    # business_push and back onto the final_score-based path.
    actor_scene = _dim(parsed, "actor_scene")
    operating_detail = _dim(parsed, "operating_detail")
    causal_arc = _dim(parsed, "causal_arc")
    transferability = _dim(parsed, "transferability")
    evidence_strength = _dim(parsed, "evidence_strength")

    business_story_fit = compute_business_story_fit(
        actor_scene, operating_detail, causal_arc, transferability, evidence_strength,
    )
    interest_flag = str(parsed["interest_flag"])
    is_promotional = interest_flag.lower() == "promotional"

    # Recompute the aggregates from the dimensions whenever the model emitted
    # numeric L1-L4 (all V3 bundles do). Legacy string-typed dimensions keep the
    # model's verbatim aggregates so old bundles and fixtures stay comparable.
    dimensions = {
        key: parsed.get(key) for key in ("L1", "L2", "L3", "L4")
    }
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in dimensions.values()
    ):
        l1, l2, l3, l4 = (float(dimensions[key]) for key in ("L1", "L2", "L3", "L4"))
        objective_quality = (
            OBJECTIVE_QUALITY_WEIGHTS["L1"] * _clip01(l1)
            + OBJECTIVE_QUALITY_WEIGHTS["L2"] * _clip01(l2)
            + OBJECTIVE_QUALITY_WEIGHTS["L3"] * _clip01(l3)
        )
        final_score = (
            FINAL_SCORE_QUALITY_WEIGHT * objective_quality
            + (1.0 - FINAL_SCORE_QUALITY_WEIGHT) * _clip01(l4)
        )
        parsed = dict(parsed)
        parsed["objective_quality"] = round(objective_quality, 4)
        parsed["final_score"] = round(final_score, 4)
        parsed["score"] = round(final_score * 10, 2)
        score = round(final_score * 10, 2)

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
        interest_flag=interest_flag,
        attribution_chain=parsed["attribution_chain"],
        actor_scene=actor_scene,
        operating_detail=operating_detail,
        causal_arc=causal_arc,
        transferability=transferability,
        evidence_strength=evidence_strength,
        business_story_fit=business_story_fit,
        is_promotional=is_promotional,
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


def _dim(parsed: dict[str, Any], field: str) -> float:
    """Read a 0-1 business-story dimension, defaulting to 0.0 when absent/invalid.

    Lenient on purpose: legacy bundles and occasionally-flaky model output must
    not crash scoring. A 0 dimension pushes the item off the business_push path,
    which is the safe fallback.
    """
    value = parsed.get(field, 0.0)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    v = float(value)
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


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
