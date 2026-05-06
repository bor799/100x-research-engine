"""Shadow-only deterministic provider for real URL pipeline validation."""

from __future__ import annotations

import json
import re

from ..models import FetchedContent, ExtractionResult, ScoreResult, TypedError


class ShadowHeuristicLLMProvider:
    """Produce prompt-contract-shaped JSON without calling an external LLM."""

    model_route = "shadow-heuristic://phase3"

    def score(self, content: FetchedContent, prompt: str) -> str | TypedError:
        profile = _score_profile(content)
        return _json(
            {
                "score": round(profile["final_score"] * 10, 2),
                "final_score": profile["final_score"],
                "signal_tier": profile["signal_tier"],
                "L1": profile["l1"],
                "L2": profile["l2"],
                "L3": profile["l3"],
                "L4": profile["l4"],
                "objective_quality": round(profile["l1"] * profile["l2"] * profile["l3"], 3),
                "decision_window_status": profile["decision_window_status"],
                "source_type": content.source_type,
                "source_tier": "public_web",
                "interest_flag": profile["interest_flag"],
                "attribution_chain": [content.source, content.url, content.content_hash],
                "rationale": profile["rationale"],
                "key_claims": _evidence_sentences(content.text, limit=3),
                "watch_items": profile["watch_items"],
            }
        )

    def extract(self, content: FetchedContent, score: ScoreResult, prompt: str) -> str | TypedError:
        title = content.title or "Untitled web article"
        evidence = _evidence_sentences(content.text, limit=4)
        actions = _actions_for_score(score)
        one_line = _one_line_signal(title, score)
        return _json(
            {
                "title": title,
                "one_line_signal": one_line,
                "decision_window_status": score.decision_window_status,
                "source_type": score.source_type,
                "source_tier": score.source_tier,
                "interest_flag": score.interest_flag,
                "attribution_chain": score.attribution_chain,
                "why_it_matters": [_why_it_matters(score)],
                "evidence": evidence,
                "inferences": [_inference_for_score(score)],
                "risks_and_conflicts": [
                    "This Phase 3 shadow provider is heuristic-only; use it to validate fetch, queue, and output behavior before enabling a real LLM route."
                ],
                "recommended_actions": actions,
                "monitoring_triggers": [
                    "New funding, customer, hiring, or product proof appears from an independent source.",
                    "A competing company announces a comparable round or deployment.",
                ],
                "obsidian_brief_markdown": _brief_markdown(
                    content,
                    score,
                    one_line=one_line,
                    evidence=evidence,
                    actions=actions,
                ),
                "url": content.url,
            }
        )

    def format_telegram(
        self,
        score: ScoreResult,
        extraction: ExtractionResult,
        prompt: str,
    ) -> str | TypedError:
        return (
            f"{extraction.title}\n"
            f"{extraction.one_line_signal}\n"
            f"Score: {score.score:g}/10 ({score.signal_tier})"
        )


def _score_profile(content: FetchedContent) -> dict[str, object]:
    title_url = f"{content.title}\n{content.url}".lower()
    text = f"{content.title}\n{content.text}".lower()
    if _contains_any(title_url, LOW_SIGNAL_TERMS):
        return {
            "final_score": 0.24,
            "signal_tier": "Reject",
            "l1": 0.35,
            "l2": 0.45,
            "l3": 0.40,
            "l4": 0.18,
            "decision_window_status": "closed",
            "interest_flag": "drop",
            "rationale": "Broad, promotional, event, or theme-led article without a narrow company-level investment action.",
            "watch_items": ["Skip unless a concrete financing, customer, or product proof emerges."],
        }
    if _contains_any(text, MARKET_CONTEXT_TERMS):
        return {
            "final_score": 0.56,
            "signal_tier": "B",
            "l1": 0.74,
            "l2": 0.76,
            "l3": 0.70,
            "l4": 0.52,
            "decision_window_status": "monitor",
            "interest_flag": "trend_watch",
            "rationale": "Market structure context is useful, but it is less actionable than a single-company financing or operating signal.",
            "watch_items": ["Map named companies into a sector watchlist.", "Look for primary-source follow-up evidence."],
        }
    if _contains_any(text, FUNDING_TERMS):
        return {
            "final_score": 0.82,
            "signal_tier": "A",
            "l1": 0.88,
            "l2": 0.90,
            "l3": 0.86,
            "l4": 0.78,
            "decision_window_status": "open",
            "interest_flag": "track",
            "rationale": "Concrete financing, valuation, investor, customer, or product details create an actionable primary-market signal.",
            "watch_items": ["Verify round details from a second source.", "Track customers, hiring, and deployment claims."],
        }
    return {
        "final_score": 0.46,
        "signal_tier": "C",
        "l1": 0.62,
        "l2": 0.60,
        "l3": 0.58,
        "l4": 0.42,
        "decision_window_status": "monitor",
        "interest_flag": "review",
        "rationale": "The article has readable content but limited primary-market actionability under the shadow heuristic.",
        "watch_items": ["Review manually for a sharper investment hook."],
    }


FUNDING_TERMS = (
    " raises ",
    " raised ",
    " series ",
    " funding ",
    " funding round",
    " valuation",
    " backed by",
    " led by ",
    " pre-ipo",
    " million",
    " billion",
)

MARKET_CONTEXT_TERMS = (
    "funding records",
    "venture funding",
    "startup investment",
    "companies that have raised",
    "foundational ai startup funding",
    "autonomous vehicle funding",
    "sector snapshot",
    "market context",
)

LOW_SIGNAL_TERMS = (
    "showcased",
    "showcase",
    "conference",
    "destination of 2026",
    "rewriting the rules",
    "/brnd/",
    "sponsored",
    "presented by",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _evidence_sentences(text: str, *, limit: int) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    scored = sorted(sentences, key=_sentence_weight, reverse=True)
    selected = [sentence.strip() for sentence in scored if 50 <= len(sentence.strip()) <= 320]
    return selected[:limit] or [normalized[:260].strip()]


def _sentence_weight(sentence: str) -> int:
    lower = sentence.lower()
    score = 0
    for term in FUNDING_TERMS + MARKET_CONTEXT_TERMS:
        if term.strip() in lower:
            score += 3
    if "$" in sentence:
        score += 2
    if re.search(r"\b\d+(?:\.\d+)?\s*(?:m|b|million|billion)\b", lower):
        score += 2
    return score


def _one_line_signal(title: str, score: ScoreResult) -> str:
    if score.signal_tier.lower() == "reject":
        return f"{title} is a weak primary-market signal and should stay out of the high-conviction queue."
    if score.final_score >= 0.75:
        return f"{title} is an actionable company-level signal worth tracking now."
    return f"{title} is useful market context, but needs sharper company-level proof before escalation."


def _why_it_matters(score: ScoreResult) -> str:
    if score.final_score >= 0.75:
        return "The article appears to contain concrete financing or operating evidence inside an active decision window."
    if score.signal_tier.lower() == "reject":
        return "The article is too broad or promotional to justify a primary-market investment brief."
    return "The article can inform sector mapping, but should not be treated as a direct single-company signal."


def _inference_for_score(score: ScoreResult) -> str:
    if score.final_score >= 0.75:
        return "The next useful step is independent verification of the round, customers, and adoption trajectory."
    if score.signal_tier.lower() == "reject":
        return "The item is better used as background reading than as an investment action trigger."
    return "Treat this as trend context until a primary source or company-specific proof point appears."


def _actions_for_score(score: ScoreResult) -> list[str]:
    if score.signal_tier.lower() == "reject":
        return ["Drop from the high-conviction queue.", "Only revisit if a specific company event follows."]
    if score.final_score >= 0.75:
        return ["Create or update the company watchlist entry.", "Verify the claims against another independent source."]
    return ["Add to sector notes.", "Wait for direct company financing, customer, or product evidence."]


def _brief_markdown(
    content: FetchedContent,
    score: ScoreResult,
    *,
    one_line: str,
    evidence: list[str],
    actions: list[str],
) -> str:
    evidence_lines = "\n".join(f"- {item}" for item in evidence)
    action_lines = "\n".join(f"- {item}" for item in actions)
    return (
        f"# {content.title}\n\n"
        f"- URL: {content.url}\n"
        f"- Source: {content.source}\n"
        f"- Signal tier: {score.signal_tier}\n"
        f"- Score: {score.score:g}/10\n\n"
        f"## One-line signal\n\n{one_line}\n\n"
        f"## Evidence\n\n{evidence_lines}\n\n"
        f"## Inference\n\n{_inference_for_score(score)}\n\n"
        f"## Recommended actions\n\n{action_lines}\n"
    )


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
