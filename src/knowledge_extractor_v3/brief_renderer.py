"""Deterministic WeChat card renderer for V4.

The card is assembled in code from the absorption payload — no LLM formatting
call. The one model-authored element that can violate trust is the quote, so
it is gated here: a quote is included only when it appears verbatim in the
fetched article. A mismatched or absent quote silently omits the section; it
can never fail the card (the old contract turned 45 good briefs into withheld
pushes this way).
"""

from __future__ import annotations

import re

from .models import FetchedContent, ScoreResult

MAX_QUOTE_CHARS = 80
MAX_ITEM_CHARS = 64
MAX_SUMMARY_CHARS = 80
MAX_TITLE_CHARS = 30
MAX_NEXT_ACTION_CHARS = 72


def render_wechat_card(
    score: ScoreResult,
    extraction,
    content: FetchedContent,
) -> str:
    """Render the channel-neutral push card from absorption results.

    ``content`` should be the ORIGINAL fetched article (not the preprocessed
    variant) so the verbatim-quote gate checks against the real source text.

    The card must fit the 500-char delivery contract (URL excluded). A fully
    loaded card with every section at cap overflows that budget (seen live on
    2026-08-17), so the renderer progressively sheds optional mass: the second
    bullets first, then the quote, then the next-step — never the required
    markers or the first signal.
    """
    # (experience limit, signal limit, keep quote, keep next action)
    for exp_limit, sig_limit, keep_quote, keep_next in (
        (2, 2, True, True),
        (1, 2, True, True),
        (1, 1, True, True),
        (1, 1, False, True),
        (1, 1, False, False),
    ):
        card = _assemble(score, extraction, content, exp_limit, sig_limit, keep_quote, keep_next)
        if _body_chars(card) <= MAX_BODY_CHARS:
            return card
    return card  # minimal card still over budget; the contract flags it


def _assemble(
    score: ScoreResult,
    extraction,
    content: FetchedContent,
    exp_limit: int,
    sig_limit: int,
    keep_quote: bool,
    keep_next: bool,
) -> str:
    parsed = extraction.parsed
    tier = score.signal_tier
    lines: list[str] = []

    lines.append(f"🎯 {_clip(str(parsed.get('title') or extraction.title), MAX_TITLE_CHARS)}")
    category = str(parsed.get("category") or "")
    lines.append(f"🏷 {_clip(str(category), MAX_ITEM_CHARS)} · Tier {tier}")
    lines.append("")
    lines.append(f"💡 {_clip(str(parsed.get('one_line_summary') or extraction.one_line_signal), MAX_SUMMARY_CHARS)}")

    experiences = _string_items(parsed.get("experiences"), limit=exp_limit)
    if experiences:
        lines.append("")
        lines.append("🗣 经验萃取")
        lines.extend(f"▪️ {_clip(item, MAX_ITEM_CHARS)}" for item in experiences)

    signals = _string_items(parsed.get("signals"), limit=sig_limit)
    if signals:
        lines.append("")
        lines.append("📡 信号萃取")
        lines.extend(f"▪️ {_clip(item, MAX_ITEM_CHARS)}" for item in signals)

    quote = str(parsed.get("quote") or "") if keep_quote else ""
    if quote and _quote_in_source(quote, content.text):
        lines.append("")
        lines.append("💬 核心金句")
        lines.append(f"\"{_clip(quote, MAX_QUOTE_CHARS)}\"")

    next_action = str(parsed.get("next_action") or "") if keep_next else ""
    if next_action:
        lines.append("")
        lines.append("🛠 下一步")
        lines.append(f"▪️ {_clip(next_action, MAX_NEXT_ACTION_CHARS)}")

    lines.append("")
    lines.append(f"🔗 阅读原文: {content.url}")
    lines.append("")
    lines.append(f"📊 评分: {score.score:.1f} · Tier {tier}")
    return "\n".join(lines)


MAX_BODY_CHARS = 500  # matches brief_contract.MAX_BRIEF_CHARS (URL excluded)
_URL_PATTERN = re.compile(r"https?://\S+")


def _body_chars(card: str) -> int:
    """Mirror the contract's length rule: URL excluded, spaces/newlines dropped."""
    body = _URL_PATTERN.sub("", card)
    return len(body.replace("\n", "").replace(" ", ""))


def _string_items(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    items = [str(item).strip() for item in value if str(item).strip()]
    return items[:limit]


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _quote_in_source(quote: str, source: str) -> bool:
    """A quote is locatable if it appears in the source, allowing for the
    whitespace/quote-character differences a model introduces."""
    if quote in source:
        return True

    def normalise(s: str) -> str:
        return re.sub(r"\s+", "", re.sub(r"[「」“”\"‘’]", "", s))

    return normalise(quote) in normalise(source)
