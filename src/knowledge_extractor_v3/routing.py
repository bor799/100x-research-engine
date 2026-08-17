"""Content routing for V4.

The score is a prioritiser, not a gate: every dimension comes from a single
absorption call and the weighted final_score ranks content into lanes. Only
two things drop an item entirely — marketing/spam verdicts and content below
the 4.0/10 floor. Everything else is at least archived to Obsidian.

  business_push    — score >= 7.5 with action_value >= 0.70 (daily WeChat lane)
  strategic_digest — score >= 7.5 that is "worth knowing" rather than "usable"
                     (weekly digest lane)
  archive_only     — 4.0 <= score < 7.5: archived, no push
  reject           — spam, or score < 4.0: dropped without an archive

Sources the operator explicitly favours (config ``routing.source_preferences``,
e.g. Elsewhere) get the push on a lower floor. The old business-story-fit /
evidence gates are replaced by a content-length floor: a fetch skeleton cannot
clear 400 characters of real text, which is what the 2026-08-16 incident class
actually looked like.
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


# V4 thresholds on final_score (0-1; score 0-10 is final_score * 10).
# Centralised so tests can pin them; each is a single tunable constant.
REJECT_FINAL_SCORE_MAX = 0.40  # below this (strictly) the item is dropped
PUSH_FINAL_SCORE_MIN = 0.75
# Within the push band, action_value decides the lane: content the reader can
# act on goes to the daily push; "worth knowing" signal goes to the weekly digest.
ACTION_VALUE_BUSINESS_MIN = 0.70

# Per-source preference floor (config `routing.source_preferences`). Favoured
# sources push on a much lower score bar, but spam still hard-rejects and the
# content must be substantial enough to rule out fetch skeletons/page shells.
DEFAULT_SOURCE_PREFERENCE_MIN_FINAL_SCORE = 0.20
PREFERENCE_MIN_CONTENT_CHARS = 400


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
    reason: str


def decide_route(
    *,
    final_score: float,
    action_value: float = 0.0,
    is_spam: bool = False,
    source: str = "",
    url: str = "",
    content_chars: int = 0,
    source_preferences: Mapping[str, SourcePreference] | None = None,
) -> RouteDecision:
    """Apply the V4 four-way routing rules.

    Order matters: spam rejects before anything else (no override can rescue
    it), then the push band splits by action_value, then the per-source
    preference override rescues favoured channels below the push bar, then the
    archive band, then reject.
    """
    if is_spam:
        return RouteDecision(
            route=Route.REJECT,
            reason="is_spam=true: marketing/shill content hard-rejects regardless of score",
        )

    if final_score >= PUSH_FINAL_SCORE_MIN:
        if action_value >= ACTION_VALUE_BUSINESS_MIN:
            return RouteDecision(
                route=Route.BUSINESS_PUSH,
                reason=(
                    f"final_score={final_score:.2f}>={PUSH_FINAL_SCORE_MIN:.2f} "
                    f"with action_value={action_value:.2f}>={ACTION_VALUE_BUSINESS_MIN:.2f}"
                ),
            )
        return RouteDecision(
            route=Route.STRATEGIC_DIGEST,
            reason=(
                f"final_score={final_score:.2f}>={PUSH_FINAL_SCORE_MIN:.2f} "
                f"but action_value={action_value:.2f}<{ACTION_VALUE_BUSINESS_MIN:.2f} (worth knowing, not usable)"
            ),
        )

    preference = _match_source_preference(source, url, source_preferences)
    if preference is not None:
        if content_chars < PREFERENCE_MIN_CONTENT_CHARS:
            return RouteDecision(
                route=Route.ARCHIVE_ONLY if final_score >= REJECT_FINAL_SCORE_MAX else Route.REJECT,
                reason=(
                    f"preferred source {source!r} blocked: content_chars={content_chars}"
                    f"<{PREFERENCE_MIN_CONTENT_CHARS} (fetch-skeleton guard)"
                ),
            )
        if final_score >= preference.min_final_score:
            return RouteDecision(
                route=Route.BUSINESS_PUSH,
                reason=(
                    f"preferred source {source!r}: final_score={final_score:.2f}"
                    f">={preference.min_final_score:.2f} (user preference override)"
                ),
            )

    if final_score >= REJECT_FINAL_SCORE_MAX:
        return RouteDecision(
            route=Route.ARCHIVE_ONLY,
            reason=(
                f"final_score={final_score:.2f} in archive band "
                f"[{REJECT_FINAL_SCORE_MAX:.2f}, {PUSH_FINAL_SCORE_MIN:.2f})"
            ),
        )

    return RouteDecision(
        route=Route.REJECT,
        reason=f"final_score={final_score:.2f}<{REJECT_FINAL_SCORE_MAX:.2f} (below absorption floor)",
    )


def route_from_score(
    score,
    *,
    source: str = "",
    url: str = "",
    content_chars: int = 0,
    source_preferences: Mapping[str, SourcePreference] | None = None,
) -> RouteDecision:
    """Convenience wrapper: build a RouteDecision from a ScoreResult.

    Accepts any object exposing the fields the parser populates; kept loose so
    tests can pass a lightweight stand-in.
    """
    return decide_route(
        final_score=score.final_score,
        action_value=_clip01(getattr(score, "action_value", 0.0)),
        is_spam=bool(
            getattr(score, "is_spam", False) or getattr(score, "is_promotional", False)
        ),
        source=source,
        url=url,
        content_chars=content_chars,
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
