"""Hard contract for channel-neutral delivery briefs.

Telegram established the accepted content product; WeChat is only a transport.
The brief therefore keeps one channel-neutral structure and gets a
non-negotiable format check after the model produces it. On failure the
Obsidian archive still succeeds, but the item is kept out of the delivery
outbox and the failure is recorded.

Rules:
  - 300–500 字 target, 500 字 hard max; shorter is allowed when the source
    genuinely has little to say (padding is forbidden, not brevity).
  - Must contain a complete plain URL.
  - Must preserve the title, judgement, signal, and source/compression anchors
    of the accepted Telegram template. The experience section remains optional
    when the source contains no reusable experience.
  - An original quote, when present, must be locatable verbatim in the source.
"""

from __future__ import annotations

import re

REQUIRED_SECTION_MARKERS = (
    "🎯",
    "💡",
    "📡 2. 信号萃取",
    "🧭 3. 信源与压缩",
)

# Forbidden filler phrases that add no decision-relevant signal.
FORBIDDEN_FILLER = (
    "这篇文章主要讨论",
    "随着AI的发展",
    "随着AI的",
    "值得注意的是",
    "需要指出的是",
    "总的来说",
    "综上所述",
)

URL_PATTERN = re.compile(r"https?://\S+")
MAX_BRIEF_CHARS = 500


def validate_brief(brief_text: str, source_text: str = "") -> list[str]:
    """Return a list of contract violations. Empty list means the brief passes.

    Args:
        brief_text: the formatted channel-neutral delivery brief.
        source_text: the original fetched article text, used to verify quotes.
    """
    errors: list[str] = []
    if not brief_text or not brief_text.strip():
        errors.append("brief is empty")
        return errors

    text = brief_text.strip()

    # 1. Hard length cap (500 字). The URL line does not count toward the cap.
    body_for_length = URL_PATTERN.sub("", text)
    body_chars = len(body_for_length.replace("\n", "").replace(" ", ""))
    if body_chars > MAX_BRIEF_CHARS:
        errors.append(
            f"brief exceeds {MAX_BRIEF_CHARS} chars (got {body_chars}); trim to 300-500"
        )

    # 2. Must contain a complete plain URL.
    if not URL_PATTERN.search(text):
        errors.append("brief is missing a complete plain URL")

    # 3. Preserve the accepted structured brief. Experience, quote, and next
    # action may be omitted when the source genuinely has no such information.
    for marker in REQUIRED_SECTION_MARKERS:
        if marker not in text:
            errors.append(f"brief is missing required section marker: {marker!r}")

    # 4. No forbidden filler phrases.
    lowered = text.lower()
    for filler in FORBIDDEN_FILLER:
        if filler.lower() in lowered:
            errors.append(f"brief contains filler phrase: {filler!r}")
            break

    # 5. Quoted spans must be locatable in the source.
    if source_text:
        for quote in _extract_quotes(text):
            if not _quote_in_source(quote, source_text):
                errors.append(
                    f"quoted span not found verbatim in source: {quote[:40]!r}..."
                )
                break

    return errors


def _extract_quotes(text: str) -> list[str]:
    """Pull out quoted spans (「...」or "...") from the brief."""
    spans: list[str] = []
    for match in re.finditer(r"[「“\"]([^」”\"]{4,})[」”\"]", text):
        spans.append(match.group(1))
    return spans


def _quote_in_source(quote: str, source: str) -> bool:
    """A quote is locatable if it appears in the source, allowing for the
    whitespace/quote-character differences a model introduces."""
    if quote in source:
        return True
    # Normalise: collapse whitespace and strip quote punctuation.
    normalise = lambda s: re.sub(r"\s+", "", re.sub(r"[「」""\"‘’]", "", s))
    return normalise(quote) in normalise(source)
