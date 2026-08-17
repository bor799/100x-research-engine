"""Load and pin the single V4 absorption prompt.

V4 has exactly one prompt (``prompts/absorption.md``); the V3 multi-bundle
registry machinery is gone. Every consumer (pipeline, health, live gate,
runtime fingerprint) loads it through here so the prompt hash used for
fingerprinting and observability is computed identically everywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .models import sha256_text

PROMPT_BUNDLE = "v4_absorption"
PROMPT_PATH = Path("prompts") / "absorption.md"


class AbsorptionPromptError(RuntimeError):
    """Raised when the absorption prompt file is missing or unreadable."""


@dataclass(frozen=True)
class AbsorptionPrompt:
    text: str
    prompt_hash: str
    path: Path

    @property
    def bundle(self) -> str:
        return PROMPT_BUNDLE


def load_absorption_prompt(project_root: Path) -> AbsorptionPrompt:
    path = Path(project_root) / PROMPT_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AbsorptionPromptError(f"Cannot read absorption prompt at {path}: {exc}") from exc
    if not text.strip():
        raise AbsorptionPromptError(f"Absorption prompt is empty: {path}")
    return AbsorptionPrompt(text=text, prompt_hash=sha256_text(text, length=16), path=path)
