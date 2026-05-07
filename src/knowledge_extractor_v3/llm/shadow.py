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
        payload = {
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
        if _is_rimbo_prompt(prompt):
            payload.update(_rimbo_score_fields(content, profile))
        return _json(payload)

    def extract(self, content: FetchedContent, score: ScoreResult, prompt: str) -> str | TypedError:
        title = content.title or "Untitled web article"
        evidence = _evidence_sentences(content.text, limit=4)
        actions = _actions_for_score(score)
        one_line = _one_line_signal(title, score)
        payload = {
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
        if _is_rimbo_prompt(prompt):
            one_line = _rimbo_one_line_signal(title, score)
            actions = _rimbo_actions_for_score(score)
            source_score = _source_score_from_score(score)
            compression = _content_compression(content, evidence)
            payload.update(
                {
                    "one_line_signal": one_line,
                    "source_score": source_score,
                    "content_compression": compression,
                    "why_it_matters": [_rimbo_why_it_matters(score)],
                    "evidence": _rimbo_evidence(content, evidence),
                    "inferences": [
                        {
                            "inference": _rimbo_inference_for_score(score),
                            "based_on": ["E1"],
                            "confidence": round(min(0.9, max(0.35, score.final_score)), 2),
                        }
                    ],
                    "risks_and_conflicts": [
                        "shadow provider 只用于验证链路和输出结构，不代表真实模型对文章质量的判断。"
                    ],
                    "recommended_actions": actions,
                    "monitoring_triggers": [
                        "出现来自独立信源的新融资、客户、招聘、产品或监管证据。",
                        "竞品宣布可对比的融资、客户部署或分发进展。",
                    ],
                    "obsidian_brief_markdown": _rimbo_brief_markdown(
                        content,
                        score,
                        one_line=one_line,
                        evidence=evidence,
                        actions=actions,
                        source_score=source_score,
                        compression=compression,
                    ),
                }
            )
        return _json(payload)

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


def _is_rimbo_prompt(prompt: str) -> bool:
    return "Rimbo Source-Scored V3" in prompt or "source_score" in prompt


def _rimbo_score_fields(content: FetchedContent, profile: dict[str, object]) -> dict[str, object]:
    source_score = {
        "D1_score": 0.55,
        "D2_score": 0.45,
        "D3_score": round(float(profile["l1"]), 2),
        "D4_score": 0.70,
        "D5_score": 0.60,
        "L1_score": round(float(profile["l1"]), 2),
        "credibility_trend": "Stable",
        "status": "active",
        "score_basis": "基于 fixture/web 元数据与文章具体性的 shadow 启发式判断",
        "rationale": "该分数仅用于验证 prompt contract 与链路，不代表真实模型评估。",
    }
    return {
        "source_type": _rimbo_source_type(content),
        "source_tier": _rimbo_source_tier(content),
        "interest_flag": _rimbo_interest_flag(profile),
        "source_score": source_score,
        "content_compression": _content_compression(
            content,
            _evidence_sentences(content.text, limit=3),
        ),
    }


def _rimbo_source_type(content: FetchedContent) -> str:
    if content.source_type == "fixture":
        return "IndustrySignal"
    if "github.com" in content.url.lower():
        return "OpenSource"
    if content.source_type in {"rss", "web_article"}:
        return "IndustryMedia"
    return "Unknown"


def _rimbo_source_tier(content: FetchedContent) -> str:
    if _rimbo_source_type(content) in {"IndustrySignal", "OpenSource"}:
        return "T3_Frontline"
    if _rimbo_source_type(content) == "IndustryMedia":
        return "T2_Professional"
    return "Unknown"


def _rimbo_interest_flag(profile: dict[str, object]) -> str:
    flag = str(profile["interest_flag"]).lower()
    if flag in {"drop", "review"}:
        return "Related"
    return "Independent"


def _source_score_from_score(score: ScoreResult) -> dict[str, object]:
    raw = score.parsed.get("source_score")
    if isinstance(raw, dict):
        return raw
    return {
        "D1_score": 0.50,
        "D2_score": 0.45,
        "D3_score": round(score.final_score, 2),
        "D4_score": 0.60,
        "D5_score": 0.55,
        "L1_score": round(score.final_score, 2),
        "credibility_trend": "Stable",
        "status": "active",
        "score_basis": "fallback shadow score",
        "rationale": "评分阶段没有提供嵌套 source_score，因此使用 shadow fallback。",
    }


def _content_compression(content: FetchedContent, evidence: list[str]) -> dict[str, object]:
    text_len = max(1, len(content.text))
    compressed = " ".join(evidence)[:320].strip()
    return {
        "compressed_signal": compressed or content.title,
        "compression_ratio_estimate": round(min(1.0, max(0.02, len(compressed) / text_len)), 2),
        "kept_facts": evidence[:3],
        "dropped_noise": [
            "shadow 压缩器省略了通用背景、重复铺垫和非决策相关表述。"
        ],
    }


def _rimbo_evidence(content: FetchedContent, evidence: list[str]) -> list[dict[str, object]]:
    return [
        {
            "id": f"E{index}",
            "claim": item,
            "source": content.source,
            "provenance": content.url,
            "confidence": 0.72,
        }
        for index, item in enumerate(evidence, start=1)
    ]


def _rimbo_one_line_signal(title: str, score: ScoreResult) -> str:
    if score.signal_tier.lower() == "reject":
        return f"{title} 暂时不足以进入高置信队列，更适合作为背景信息保留。"
    if score.final_score >= 0.75:
        return f"{title} 提供了值得立即跟踪的公司级信号。"
    return f"{title} 有一定市场参考价值，但还需要更直接的公司级证据才能升级。"


def _rimbo_why_it_matters(score: ScoreResult) -> str:
    if score.final_score >= 0.75:
        return "内容包含较具体的融资、运营或客户证据，可能仍处在可行动窗口内。"
    if score.signal_tier.lower() == "reject":
        return "内容过宽或偏营销，不足以支撑一级市场投资简报。"
    return "内容可用于赛道观察，但暂时不应当被视为单一公司的直接行动信号。"


def _rimbo_inference_for_score(score: ScoreResult) -> str:
    if score.final_score >= 0.75:
        return "下一步应独立核验融资、客户、采用率和运营证据。"
    if score.signal_tier.lower() == "reject":
        return "该条更适合作为背景阅读，而不是投资行动触发器。"
    return "先作为趋势上下文处理，等待一手信源或更具体的公司级证据。"


def _rimbo_actions_for_score(score: ScoreResult) -> list[str]:
    if score.signal_tier.lower() == "reject":
        return ["从高置信队列移除。", "只有出现具体公司事件时再回看。"]
    if score.final_score >= 0.75:
        return ["创建或更新公司观察条目。", "用另一个独立信源核验关键主张。"]
    return ["加入赛道笔记。", "等待直接的融资、客户、产品或运营证据。"]


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


def _rimbo_brief_markdown(
    content: FetchedContent,
    score: ScoreResult,
    *,
    one_line: str,
    evidence: list[str],
    actions: list[str],
    source_score: dict[str, object],
    compression: dict[str, object],
) -> str:
    source_lines = "\n".join(
        [
            f"- 层级: {score.source_tier}",
            f"- 类型: {score.source_type}",
            f"- 利益标记: {score.interest_flag}",
            f"- L1: {source_score.get('L1_score', score.final_score)}",
            (
                "- D1-D5: "
                f"{source_score.get('D1_score')} / "
                f"{source_score.get('D2_score')} / "
                f"{source_score.get('D3_score')} / "
                f"{source_score.get('D4_score')} / "
                f"{source_score.get('D5_score')}"
            ),
        ]
    )
    kept_facts = compression.get("kept_facts")
    fact_items = kept_facts if isinstance(kept_facts, list) else evidence
    fact_lines = "\n".join(f"- {item}" for item in fact_items)
    evidence_lines = "\n".join(f"- E{index}: {item}" for index, item in enumerate(evidence, start=1))
    action_lines = "\n".join(f"- {item}" for item in actions)
    trigger_lines = "\n".join(
        [
            "- 出现来自独立信源的新融资、客户、招聘、产品或监管证据。",
            "- 竞品宣布可对比的融资、客户部署或分发进展。",
        ]
    )
    return (
        f"# {content.title}\n\n"
        f"- URL: {content.url}\n"
        f"- Score: {score.score:g}/10 ({score.signal_tier})\n\n"
        f"## 信号\n\n{one_line}\n\n"
        f"## 信源评分\n\n{source_lines}\n\n"
        f"## 压缩事实\n\n{fact_lines}\n\n"
        f"## 证据\n\n{evidence_lines}\n\n"
        f"## 推断\n\n{_rimbo_inference_for_score(score)}\n\n"
        "## 风险与利益冲突\n\n"
        "- shadow provider 只用于验证链路和输出结构，不代表真实模型对文章质量的判断。\n\n"
        f"## 下一步行动\n\n{action_lines}\n\n"
        f"## 监控触发器\n\n{trigger_lines}\n"
    )


def _json(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)
