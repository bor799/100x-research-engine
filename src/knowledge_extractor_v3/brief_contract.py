"""Hard contract for channel-neutral delivery briefs.

The V4 card is rendered deterministically in code (brief_renderer), so this
check is a safety net against renderer bugs, not a judge of model output. The
verbatim-quote rule moved into the renderer, where a mismatch omits the quote
section instead of failing the whole brief (the V3 contract withheld 45 good
pushes that way).

Rules:
  - 300–500 字 target, 500 字 hard max; shorter is allowed when the source
    genuinely has little to say (padding is forbidden, not brevity).
  - Must contain a complete plain URL.
  - Must preserve the title, summary, and signal anchors of the card template.
    Experience, quote, and next-step sections are legitimately omittable.
"""

from __future__ import annotations

import re

REQUIRED_SECTION_MARKERS = (
    "🎯",
    "💡",
    "📡 信号萃取",
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


def validate_brief(brief_text: str) -> list[str]:
    """Return a list of contract violations. Empty list means the brief passes."""
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

    return errors

