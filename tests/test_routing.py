"""V4 routing: score bands, lane split, spam hard-reject, preference override."""

from __future__ import annotations

from knowledge_extractor_v3.routing import (
    ACTION_VALUE_BUSINESS_MIN,
    PREFERENCE_MIN_CONTENT_CHARS,
    PUSH_FINAL_SCORE_MIN,
    REJECT_FINAL_SCORE_MAX,
    Route,
    SourcePreference,
    decide_route,
    route_from_score,
)

PREFS = {
    "elsewhere": SourcePreference(
        source="elsewhere",
        min_final_score=0.20,
        url_prefixes=("https://elsewhere.news/",),
    )
}


class _Score:
    """Lightweight ScoreResult stand-in for route_from_score."""

    def __init__(
        self,
        final_score: float,
        *,
        action_value: float = 0.0,
        is_spam: bool = False,
    ):
        self.final_score = final_score
        self.action_value = action_value
        self.is_spam = is_spam
        self.is_promotional = is_spam


def test_push_band_boundaries():
    # 0.749 archives, 0.75 pushes
    assert decide_route(final_score=0.749, action_value=0.9).route is Route.ARCHIVE_ONLY
    assert decide_route(final_score=0.75, action_value=0.9).route is Route.BUSINESS_PUSH


def test_reject_floor_boundaries():
    # 0.60 archives, 0.599 rejects — the only score-based drop
    assert decide_route(final_score=0.60, action_value=0.0).route is Route.ARCHIVE_ONLY
    assert decide_route(final_score=0.599, action_value=0.9).route is Route.REJECT


def test_push_band_splits_by_action_value():
    high_action = decide_route(final_score=0.80, action_value=ACTION_VALUE_BUSINESS_MIN)
    assert high_action.route is Route.BUSINESS_PUSH

    low_action = decide_route(final_score=0.80, action_value=ACTION_VALUE_BUSINESS_MIN - 0.01)
    assert low_action.route is Route.STRATEGIC_DIGEST
    assert "worth knowing" in low_action.reason


def test_spam_hard_rejects_even_with_high_score():
    decision = decide_route(final_score=0.95, action_value=0.9, is_spam=True)
    assert decision.route is Route.REJECT
    assert "spam" in decision.reason.lower()


def test_preference_rescues_below_push_bar():
    decision = decide_route(
        final_score=0.25,
        action_value=0.2,
        source="elsewhere",
        url="https://elsewhere.news/zh/article",
        content_chars=800,
        source_preferences=PREFS,
    )
    assert decision.route is Route.BUSINESS_PUSH
    assert "preference" in decision.reason


def test_preference_url_prefix_matches_manual_submissions():
    # Manual enqueue uses source='cindy_wechat'; the URL prefix carries the
    # channel preference (production Elsewhere case).
    decision = decide_route(
        final_score=0.30,
        action_value=0.3,
        source="cindy_wechat",
        url="https://elsewhere.news/zh/funeralai/kimi-1786944734501",
        content_chars=2000,
        source_preferences=PREFS,
    )
    assert decision.route is Route.BUSINESS_PUSH


def test_preference_blocked_by_fetch_skeleton_guard():
    # Under 400 chars of content: no preference push. 0.25 falls to reject.
    decision = decide_route(
        final_score=0.25,
        action_value=0.5,
        source="elsewhere",
        url="https://elsewhere.news/zh/article",
        content_chars=120,
        source_preferences=PREFS,
    )
    assert decision.route is Route.REJECT
    assert "fetch-skeleton" in decision.reason

    # Same skeleton at an archive-band score still never pushes.
    archive = decide_route(
        final_score=0.65,
        action_value=0.5,
        source="elsewhere",
        url="https://elsewhere.news/zh/article",
        content_chars=120,
        source_preferences=PREFS,
    )
    assert archive.route is Route.ARCHIVE_ONLY


def test_preference_respects_custom_floor():
    decision = decide_route(
        final_score=0.15,
        action_value=0.5,
        source="elsewhere",
        url="https://elsewhere.news/zh/article",
        content_chars=800,
        source_preferences=PREFS,  # floor 0.20
    )
    assert decision.route is Route.REJECT


def test_no_preference_for_unknown_source():
    decision = decide_route(
        final_score=0.65,
        action_value=0.5,
        source="some-other-feed",
        url="https://example.com/a",
        content_chars=900,
        source_preferences=PREFS,
    )
    assert decision.route is Route.ARCHIVE_ONLY


def test_route_from_score_maps_score_result_fields():
    score = _Score(0.76, action_value=0.8)
    decision = route_from_score(score, source="feed", url="https://a.b/c", content_chars=1000)
    assert decision.route is Route.BUSINESS_PUSH

    spam = _Score(0.9, is_spam=True)
    assert route_from_score(spam).route is Route.REJECT

    legacy_promotional = _Score(0.9)
    legacy_promotional.is_promotional = True
    assert route_from_score(legacy_promotional).route is Route.REJECT


def test_threshold_constants_are_pinned():
    assert REJECT_FINAL_SCORE_MAX == 0.60
    assert PUSH_FINAL_SCORE_MIN == 0.75
    assert ACTION_VALUE_BUSINESS_MIN == 0.70
    assert PREFERENCE_MIN_CONTENT_CHARS == 400
