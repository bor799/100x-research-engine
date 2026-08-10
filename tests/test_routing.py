"""Tests for the V3 four-way routing and business_story_fit formula.

These pin the exact thresholds from the efficiency-first plan so that the
scoring → routing contract cannot drift silently.
"""

from __future__ import annotations

import math

from knowledge_extractor_v3.routing import (
    ARCHIVE_FINAL_SCORE_MIN,
    PUSH_BSF_MIN,
    PUSH_EVIDENCE_MIN,
    PUSH_L1_MIN,
    PUSH_OPERATING_DETAIL_MIN,
    STRATEGIC_FINAL_SCORE_MIN,
    Route,
    compute_business_story_fit,
    decide_route,
    route_from_score,
)


# ---------------------------------------------------------------------------
# business_story_fit formula
# ---------------------------------------------------------------------------


def test_bsf_uses_exact_weights_from_plan():
    # All dims at 1.0 -> fit = 1.0
    assert math.isclose(
        compute_business_story_fit(1, 1, 1, 1, 1), 1.0, abs_tol=1e-9
    )
    # operating_detail carries the heaviest single weight (0.30)
    fit = compute_business_story_fit(0, 1, 0, 0, 0)
    assert math.isclose(fit, 0.30, abs_tol=1e-9)
    # actor_scene + causal_arc tie at 0.20
    assert math.isclose(compute_business_story_fit(1, 0, 0, 0, 0), 0.20, abs_tol=1e-9)
    assert math.isclose(compute_business_story_fit(0, 0, 1, 0, 0), 0.20, abs_tol=1e-9)
    # transferability + evidence_strength tie at 0.15
    assert math.isclose(compute_business_story_fit(0, 0, 0, 1, 0), 0.15, abs_tol=1e-9)
    assert math.isclose(compute_business_story_fit(0, 0, 0, 0, 1), 0.15, abs_tol=1e-9)


def test_bsf_clamps_out_of_range_to_01():
    # actor_scene=2->1.0 (0.20), operating_detail=-1->0.0 (0), causal_arc=5->1.0 (0.20)
    assert math.isclose(compute_business_story_fit(2, -1, 5, 0, 0), 0.40, abs_tol=1e-9)


# ---------------------------------------------------------------------------
# Four-way routing boundaries
# ---------------------------------------------------------------------------


def _decision(**overrides):
    base = dict(
        final_score=0.5,
        l1=0.5,
        is_promotional=False,
        business_story_fit=0.5,
        operating_detail=0.5,
        evidence_strength=0.5,
    )
    base.update(overrides)
    return decide_route(**base)


def test_business_push_when_all_thresholds_met():
    d = _decision(
        final_score=0.50,  # low overall score is OK — the point of the bypass
        l1=PUSH_L1_MIN,
        business_story_fit=PUSH_BSF_MIN,
        operating_detail=PUSH_OPERATING_DETAIL_MIN,
        evidence_strength=PUSH_EVIDENCE_MIN,
    )
    assert d.route is Route.BUSINESS_PUSH


def test_business_push_bypasses_low_final_score():
    """A first-hand operating story must not be killed by a low source tier."""
    d = _decision(
        final_score=0.45,
        l1=PUSH_L1_MIN,
        business_story_fit=0.90,
        operating_detail=0.80,
        evidence_strength=0.70,
    )
    assert d.route is Route.BUSINESS_PUSH


def test_business_push_blocked_when_promotional():
    """Funding PR with names+numbers but no honest mechanism is filtered out."""
    d = _decision(
        final_score=0.85,
        l1=0.80,
        business_story_fit=0.90,
        operating_detail=0.80,
        evidence_strength=0.70,
        is_promotional=True,
    )
    assert d.route is not Route.BUSINESS_PUSH
    # Still high overall signal -> strategic digest, not wasted
    assert d.route is Route.STRATEGIC_DIGEST


def test_business_push_blocked_when_operating_detail_low():
    d = _decision(
        final_score=0.50,
        l1=0.80,
        business_story_fit=PUSH_BSF_MIN,
        operating_detail=0.40,  # below 0.70
        evidence_strength=0.70,
    )
    assert d.route is not Route.BUSINESS_PUSH


def test_business_push_blocked_when_l1_below_source_floor():
    d = _decision(
        final_score=0.50,
        l1=0.40,  # below 0.45
        business_story_fit=PUSH_BSF_MIN,
        operating_detail=0.80,
        evidence_strength=0.70,
    )
    assert d.route is not Route.BUSINESS_PUSH


def test_strategic_digest_for_high_final_score_non_business():
    d = _decision(
        final_score=STRATEGIC_FINAL_SCORE_MIN,
        l1=0.80,
        business_story_fit=0.40,  # not a qualifying business story
    )
    assert d.route is Route.STRATEGIC_DIGEST


def test_archive_only_in_the_0_70_to_0_80_band():
    d = _decision(
        final_score=ARCHIVE_FINAL_SCORE_MIN,
        l1=0.50,
        business_story_fit=0.30,
    )
    assert d.route is Route.ARCHIVE_ONLY


def test_reject_below_archive_floor_without_business_fit():
    d = _decision(
        final_score=0.65,
        l1=0.50,
        business_story_fit=0.30,
    )
    assert d.route is Route.REJECT


def test_business_push_takes_precedence_over_strategic():
    d = _decision(
        final_score=0.95,  # would qualify for strategic on its own
        l1=PUSH_L1_MIN,
        business_story_fit=PUSH_BSF_MIN,
        operating_detail=PUSH_OPERATING_DETAIL_MIN,
        evidence_strength=PUSH_EVIDENCE_MIN,
    )
    assert d.route is Route.BUSINESS_PUSH


# ---------------------------------------------------------------------------
# route_from_score wrapper
# ---------------------------------------------------------------------------


class _FakeScore:
    """Minimal stand-in matching the ScoreResult fields route_from_score reads."""

    def __init__(self, final_score, l1, **dim_kw):
        self.final_score = final_score
        self.parsed = {"L1": l1}
        self.is_promotional = dim_kw.get("is_promotional", False)
        self.business_story_fit = dim_kw.get("business_story_fit", 0.0)
        self.operating_detail = dim_kw.get("operating_detail", 0.0)
        self.evidence_strength = dim_kw.get("evidence_strength", 0.0)


def test_route_from_score_coerces_string_l1_safely():
    score = _FakeScore(final_score=0.82, l1="not a number")
    # String L1 coerces to 0 -> can't be business_push -> falls to strategic
    assert route_from_score(score).route is Route.STRATEGIC_DIGEST


def test_route_from_score_business_push_path():
    score = _FakeScore(
        final_score=0.50,
        l1=0.50,
        business_story_fit=0.85,
        operating_detail=0.75,
        evidence_strength=0.65,
    )
    assert route_from_score(score).route is Route.BUSINESS_PUSH
