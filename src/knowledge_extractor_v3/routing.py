"""Content routing for V3.

Separates "is this worth keeping?" (quality / final_score) from "should we push
it to the user?" (business-story fit). The old pipeline applied a single
non-bypassable 0.7 final_score floor that rejected everything below it —
including first-hand operating stories whose only weakness was a low source
tier. This module replaces that gate with a four-way decision:

  business_push    — a retellable, specific business story worth a WeChat push
  strategic_digest — high overall signal, surfaced in the weekly digest
  archive_only     — worth keeping in Obsidian, not worth a push
  reject           — none of the above; drop it

The thresholds are the ones fixed in the V3 efficiency-first plan so that the
behaviour is auditable in one place rather than scattered across prompts.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


class Route(str, Enum):
    BUSINESS_PUSH = "business_push"
    STRATEGIC_DIGEST = "strategic_digest"
    ARCHIVE_ONLY = "archive_only"
    REJECT = "reject"


# Weighted, computable business-story fit. The prompt no longer carries
# pseudo-weights like "2.0" / "1.8" that the code cannot evaluate; instead the
# model emits five 0-1 dimensions and the code combines them here.
BSF_WEIGHTS = {
    "actor_scene": 0.20,
    "operating_detail": 0.30,
    "causal_arc": 0.20,
    "transferability": 0.15,
    "evidence_strength": 0.15,
}

# Fixed push thresholds (from the V3 plan). Centralised so tests can pin them.
PUSH_BSF_MIN = 0.70
PUSH_OPERATING_DETAIL_MIN = 0.60
PUSH_EVIDENCE_MIN = 0.50
PUSH_L1_MIN = 0.40
STRATEGIC_FINAL_SCORE_MIN = 0.75
ARCHIVE_FINAL_SCORE_MIN = 0.70

# Per-source preference floor (config `routing.source_preferences`). Sources the
# user explicitly wants in the daily push route to business_push on a much
# lower quality bar, so a Chinese-language source the rubric underrates is not
# silently dropped. Promotional content is still rejected.
DEFAULT_SOURCE_PREFERENCE_MIN_FINAL_SCORE = 0.20


@dataclass(frozen=True)
class SourcePreference:
    """Routing override for one source, keyed by the exact sources.yaml name.

    ``url_prefixes`` extends the match to any task URL starting with one of
    them, so manually submitted links (whose task source is the submission
    channel, e.g. cindy_wechat) still get the channel preference.
    """

    source: str
    min_final_score: float = DEFAULT_SOURCE_PREFERENCE_MIN_FINAL_SCORE
    url_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    business_story_fit: float
    reason: str


def compute_business_story_fit(
    actor_scene: float,
    operating_detail: float,
    causal_arc: float,
    transferability: float,
    evidence_strength: float,
) -> float:
    """Combine the five 0-1 dimensions into a single 0-1 business-story fit."""
    return (
        BSF_WEIGHTS["actor_scene"] * _clip01(actor_scene)
        + BSF_WEIGHTS["operating_detail"] * _clip01(operating_detail)
        + BSF_WEIGHTS["causal_arc"] * _clip01(causal_arc)
        + BSF_WEIGHTS["transferability"] * _clip01(transferability)
        + BSF_WEIGHTS["evidence_strength"] * _clip01(evidence_strength)
    )


def decide_route(
    *,
    final_score: float,
    l1: float,
    is_promotional: bool,
    business_story_fit: float,
    operating_detail: float,
    evidence_strength: float,
    source: str = "",
    url: str = "",
    source_preferences: Mapping[str, SourcePreference] | None = None,
) -> RouteDecision:
    """Apply the four-way routing rules.

    Order matters: business_push is evaluated on its own merits (independent of
    final_score, so a strong first-hand story is not killed by a low source
    tier), then a per-source user preference override, then strategic_digest,
    then archive_only, then reject.
    """
    bsf = business_story_fit

    # Funding PR with names/numbers but no operating mechanism must not slip in:
    # evidence_strength + operating_detail + the non-Promotional guard filter
    # press releases out even when BSF looks passable.
    if (
        bsf >= PUSH_BSF_MIN
        and operating_detail >= PUSH_OPERATING_DETAIL_MIN
        and evidence_strength >= PUSH_EVIDENCE_MIN
        and l1 >= PUSH_L1_MIN
        and not is_promotional
    ):
        return RouteDecision(
            route=Route.BUSINESS_PUSH,
            business_story_fit=bsf,
            reason=(
                f"business_story_fit={bsf:.2f}>=0.70, "
                f"operating_detail={operating_detail:.2f}, L1={l1:.2f}"
            ),
        )

    preference = _match_source_preference(source, url, source_preferences)
    if (
        preference is not None
        and not is_promotional
        and final_score >= preference.min_final_score
    ):
        return RouteDecision(
            route=Route.BUSINESS_PUSH,
            business_story_fit=bsf,
            reason=(
                f"preferred source {source!r}: final_score={final_score:.2f}"
                f">={preference.min_final_score:.2f} (user preference override)"
            ),
        )

    if final_score >= STRATEGIC_FINAL_SCORE_MIN:
        return RouteDecision(
            route=Route.STRATEGIC_DIGEST,
            business_story_fit=bsf,
            reason=f"final_score={final_score:.2f}>=0.75 (high overall signal)",
        )

    if final_score >= ARCHIVE_FINAL_SCORE_MIN:
        return RouteDecision(
            route=Route.ARCHIVE_ONLY,
            business_story_fit=bsf,
            reason=f"final_score={final_score:.2f} in archive band (0.70-0.75)",
        )

    return RouteDecision(
        route=Route.REJECT,
        business_story_fit=bsf,
        reason=f"final_score={final_score:.2f}<0.70 and not a qualifying business story",
    )


def route_from_score(
    score,
    *,
    source: str = "",
    url: str = "",
    source_preferences: Mapping[str, SourcePreference] | None = None,
) -> RouteDecision:
    """Convenience wrapper: build a RouteDecision from a ScoreResult.

    Accepts any object exposing the dimension fields populated by the parser;
    kept loose so tests can pass a lightweight stand-in.
    """
    return decide_route(
        final_score=score.final_score,
        l1=_clip01(score.parsed.get("L1", 0.0)),
        is_promotional=getattr(score, "is_promotional", False),
        business_story_fit=getattr(score, "business_story_fit", 0.0),
        operating_detail=getattr(score, "operating_detail", 0.0),
        evidence_strength=getattr(score, "evidence_strength", 0.0),
        source=source,
        url=url,
        source_preferences=source_preferences,
    )


def _match_source_preference(
    source: str,
    url: str,
    source_preferences: Mapping[str, SourcePreference] | None,
) -> SourcePreference | None:
    if not source_preferences:
        return None
    if source:
        preference = source_preferences.get(source)
        if preference is not None:
            return preference
    if url:
        for preference in source_preferences.values():
            if any(url.startswith(prefix) for prefix in preference.url_prefixes):
                return preference
    return None


def _clip01(value: float) -> float:
    if value is None:
        return 0.0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v
