"""Hard contract for WeChat briefs.

The brief is the only thing the user sees, so it gets a non-negotiable format
check that runs after the model produces it. On failure the Obsidian archive
still succeeds (it is valuable to keep), but the item is kept out of the WeChat
outbox and the failure is recorded — the model does not get a second attempt,
because auto-rewriting on failure was the cost spiral the efficiency plan kills.

Rules (from the V3 efficiency-first plan):
  - 100–200 字 target, 300 字 hard max; shorter than 100 is allowed when the
    source genuinely has little to say (padding is forbidden, not brevity).
  - Must contain a complete plain URL.
  - No forbidden boilerplate sections (分类 / 信源说明 / 下一步 / 背景套话).
  - An original quote, when present, must be locatable verbatim in the source.
"""

from __future__ import annotations

import re

# Sections the efficiency plan explicitly removed from the brief template.
FORBIDDEN_SECTION_MARKERS = (
    "🏷",       # 分类
    "🧭",       # 信源与压缩
    "🛠",       # 下一步
    "🏷️分类",
    "分类：",
    "分类:",
    "信源说明",
    "下一步",
    "背景套话",
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
MAX_BRIEF_CHARS = 300


def validate_brief(brief_text: str, source_text: str = "") -> list[str]:
    """Return a list of contract violations. Empty list means the brief passes.

    Args:
        brief_text: the formatted WeChat brief.
        source_text: the original fetched article text, used to verify quotes.
    """
    errors: list[str] = []
    if not brief_text or not brief_text.strip():
        errors.append("brief is empty")
        return errors

    text = brief_text.strip()

    # 1. Hard length cap (300 字). The URL line does not count toward the cap.
    body_for_length = URL_PATTERN.sub("", text)
    body_chars = len(body_for_length.replace("\n", "").replace(" ", ""))
    if body_chars > MAX_BRIEF_CHARS:
        errors.append(
            f"brief exceeds {MAX_BRIEF_CHARS} chars (got {body_chars}); trim to 100-200"
        )

    # 2. Must contain a complete plain URL.
    if not URL_PATTERN.search(text):
        errors.append("brief is missing a complete plain URL")

    # 3. No forbidden boilerplate sections.
    for marker in FORBIDDEN_SECTION_MARKERS:
        if marker in text:
            errors.append(f"brief contains forbidden section marker: {marker!r}")
            break

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
