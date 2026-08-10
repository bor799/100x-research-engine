"""Shared prompt/output contract for V3 parser-compatible bundles."""

from __future__ import annotations

import re


SCORING_REQUIRED_FIELDS = (
    "score",
    "final_score",
    "signal_tier",
    "L1",
    "L2",
    "L3",
    "L4",
    "objective_quality",
    "decision_window_status",
    "source_type",
    "source_tier",
    "interest_flag",
    "attribution_chain",
    "rationale",
    "key_claims",
    "watch_items",
)

# V3 business-story dimensions (0-1 each). These are NOT in the parser's
# hard-required set, so legacy/parallel bundles whose prompts predate them still
# parse (their content simply routes on final_score alone, with a 0 business
# fit). The active v3_business_stories bundle declares them in its JSON schema,
# and the parser reads them leniently (missing/invalid -> 0.0) so a model that
# drops one field degrades to "not a business push" rather than crashing the task.
V3_BUSINESS_DIMENSIONS = (
    "actor_scene",
    "operating_detail",
    "causal_arc",
    "transferability",
    "evidence_strength",
)

EXTRACTION_REQUIRED_FIELDS = (
    "title",
    "one_line_signal",
    "decision_window_status",
    "source_type",
    "source_tier",
    "interest_flag",
    "attribution_chain",
    "why_it_matters",
    "evidence",
    "inferences",
    "risks_and_conflicts",
    "recommended_actions",
    "monitoring_triggers",
    "obsidian_brief_markdown",
)

ROLE_REQUIRED_FIELDS = {
    "scoring": SCORING_REQUIRED_FIELDS,
    "extraction": EXTRACTION_REQUIRED_FIELDS,
}


def missing_prompt_contract_fields(prompt_text: str, role: str) -> tuple[str, ...]:
    """Return V3 fields that the prompt does not declare as JSON object keys."""
    required = ROLE_REQUIRED_FIELDS.get(role)
    if required is None:
        return ()
    return tuple(
        field
        for field in required
        if re.search(rf'"{re.escape(field)}"\s*:', prompt_text) is None
    )
