"""Load and pin the V4 increment-audit prompt.

The increment call compares an archived article against a re-fetched version
of the same URL and reports what actually changed. It is loaded through this
pinned module for the same reason as ``absorption_prompt``: one hash, one
bundle name, computed identically everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import sha256_text

PROMPT_BUNDLE = "v4_increment"
PROMPT_PATH = Path("prompts") / "increment.md"


class IncrementPromptError(RuntimeError):
    """Raised when the increment prompt file is missing or unreadable."""


@dataclass(frozen=True)
class IncrementPrompt:
    text: str
    prompt_hash: str
    path: Path

    @property
    def bundle(self) -> str:
        return PROMPT_BUNDLE


def load_increment_prompt(project_root: Path) -> IncrementPrompt:
    path = Path(project_root) / PROMPT_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IncrementPromptError(f"Cannot read increment prompt at {path}: {exc}") from exc
    if not text.strip():
        raise IncrementPromptError(f"Increment prompt is empty: {path}")
    return IncrementPrompt(text=text, prompt_hash=sha256_text(text, length=16), path=path)
