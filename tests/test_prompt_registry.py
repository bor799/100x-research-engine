from pathlib import Path

import pytest

from knowledge_extractor_v3.prompt_registry import PromptRegistry, PromptRegistryError


ROOT = Path(__file__).resolve().parents[1]


def test_prompt_registry_validates_active_and_parallel_bundles():
    registry = PromptRegistry.default(ROOT)

    registry.validate()

    parallel_bundle_names = [bundle.name for bundle in registry.bundles_for_parallel_test()]
    assert registry.active_bundle_name == "v3_business_stories_v2"
    assert registry.active_bundle_name in parallel_bundle_names
    assert parallel_bundle_names[0] == "primary_market_v1"
    assert "rimbo_source_scored_v3" in parallel_bundle_names
    assert "v2_legacy" in parallel_bundle_names
    assert "v2_stable_cn" in parallel_bundle_names
    assert "v3_business_stories" in parallel_bundle_names


def test_prompt_registry_loads_scoring_and_extraction_roles():
    registry = PromptRegistry.default(ROOT)

    scoring = registry.load_prompt("primary_market_v1", "scoring")
    extraction = registry.load_prompt("primary_market_v1", "extraction")
    rimbo_scoring = registry.load_prompt("rimbo_source_scored_v3", "scoring")
    rimbo_extraction = registry.load_prompt("rimbo_source_scored_v3", "extraction")
    legacy_scoring = registry.load_prompt("v2_legacy", "scoring")
    stable_scoring = registry.load_prompt("v2_stable_cn", "scoring")
    stable_extraction = registry.load_prompt("v2_stable_cn", "extraction")
    business_scoring = registry.load_prompt("v3_business_stories", "scoring")
    business_extraction = registry.load_prompt("v3_business_stories", "extraction")
    delivery_brief = registry.load_prompt("v3_business_stories", "telegram_brief")

    assert "final_score" in scoring
    assert "obsidian_brief_markdown" in extraction
    assert "source_score" in rimbo_scoring
    assert "content_compression" in rimbo_extraction
    assert "商业化/变现实操" in legacy_scoring
    assert "商业化/变现实操" in stable_scoring
    assert "source_score" in stable_scoring
    assert "content_compression" in stable_extraction
    assert "| 可转述商业故事 | 2.0 |" in business_scoring
    assert "| 小生意/一线经营 | 1.8 |" in business_scoring
    # The five computable dimensions replace the non-computable prose weights.
    assert "actor_scene" in business_scoring
    assert "operating_detail" in business_scoring
    assert "causal_arc" in business_scoring
    assert "transferability" in business_scoring
    assert "evidence_strength" in business_scoring
    # The macro-narrative veto was removed: high-value strategic analysis must
    # stay reachable via strategic_digest / archive_only even without a small
    # business story.
    assert "只有宏大叙事和行业概述" not in business_scoring
    assert "必须是一句可被转述的话" in business_scoring
    assert "## 遣词风格" in business_extraction
    assert "GLM" not in delivery_brief  # model choice stays in config, not content
    assert "🗣 1. 经验萃取" in delivery_brief
    assert "📡 2. 信号萃取" in delivery_brief
    assert "🧭 3. 信源与压缩" in delivery_brief
    assert "🛠 5. 下一步" in delivery_brief
    assert "300-500 字" in delivery_brief
    assert registry.bundle("v3_business_stories").prompt_path("telegram_brief").name == "telegram_brief.md"


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


def test_v3_business_stories_satisfies_active_parser_contract():
    registry = PromptRegistry.default(ROOT)

    registry.validate_active_contract()


def test_v2_legacy_cannot_become_active_parser_bundle():
    registry = PromptRegistry(
        ROOT / "prompts" / "registry.json",
        active_bundle="v2_legacy",
    )

    with pytest.raises(PromptRegistryError, match="incompatible with the V3 parser contract"):
        registry.validate_active_contract()
