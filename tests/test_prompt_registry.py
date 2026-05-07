from pathlib import Path

from knowledge_extractor_v3.prompt_registry import PromptRegistry


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_registry_validates_active_and_parallel_bundles():
    registry = PromptRegistry.default(ROOT)

    registry.validate()

    assert registry.active_bundle_name == "primary_market_v1"
    parallel_bundle_names = [bundle.name for bundle in registry.bundles_for_parallel_test()]
    assert parallel_bundle_names[0] == "primary_market_v1"
    assert "rimbo_source_scored_v3" in parallel_bundle_names
    assert "v2_legacy" in parallel_bundle_names


def test_prompt_registry_loads_scoring_and_extraction_roles():
    registry = PromptRegistry.default(ROOT)

    scoring = registry.load_prompt("primary_market_v1", "scoring")
    extraction = registry.load_prompt("primary_market_v1", "extraction")
    rimbo_scoring = registry.load_prompt("rimbo_source_scored_v3", "scoring")
    rimbo_extraction = registry.load_prompt("rimbo_source_scored_v3", "extraction")
    legacy_scoring = registry.load_prompt("v2_legacy", "scoring")

    assert "final_score" in scoring
    assert "obsidian_brief_markdown" in extraction
    assert "source_score" in rimbo_scoring
    assert "content_compression" in rimbo_extraction
    assert "商业化/变现实操" in legacy_scoring


def test_prompt_registry_keeps_scoring_and_extraction_separate():
    registry = PromptRegistry.default(ROOT)
    bundle = registry.bundle("primary_market_v1")

    assert bundle.prompt_path("scoring").name == "scoring.md"
    assert bundle.prompt_path("extraction").name == "extraction.md"
    assert bundle.prompt_path("scoring") != bundle.prompt_path("extraction")


def test_prompt_registry_can_override_active_and_parallel_from_config():
    class PromptsConfig:
        registry = "prompts/registry.json"
        active_bundle = "rimbo_source_scored_v3"
        parallel_test_bundles = ["rimbo_source_scored_v3"]

    registry = PromptRegistry.from_config(ROOT, PromptsConfig())

    registry.validate()

    assert registry.active_bundle_name == "rimbo_source_scored_v3"
    assert [bundle.name for bundle in registry.bundles_for_parallel_test()] == [
        "rimbo_source_scored_v3",
    ]
