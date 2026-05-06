from pathlib import Path

from knowledge_extractor_v3.prompt_registry import PromptRegistry


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_registry_validates_active_and_parallel_bundles():
    registry = PromptRegistry.default(ROOT)

    registry.validate()

    assert registry.active_bundle_name == "primary_market_v1"
    assert [bundle.name for bundle in registry.bundles_for_parallel_test()] == [
        "primary_market_v1",
        "v2_legacy",
    ]


def test_prompt_registry_loads_scoring_and_extraction_roles():
    registry = PromptRegistry.default(ROOT)

    scoring = registry.load_prompt("primary_market_v1", "scoring")
    extraction = registry.load_prompt("primary_market_v1", "extraction")
    legacy_scoring = registry.load_prompt("v2_legacy", "scoring")

    assert "final_score" in scoring
    assert "obsidian_brief_markdown" in extraction
    assert "商业化/变现实操" in legacy_scoring


def test_prompt_registry_keeps_scoring_and_extraction_separate():
    registry = PromptRegistry.default(ROOT)
    bundle = registry.bundle("primary_market_v1")

    assert bundle.prompt_path("scoring").name == "scoring.md"
    assert bundle.prompt_path("extraction").name == "extraction.md"
    assert bundle.prompt_path("scoring") != bundle.prompt_path("extraction")
