from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "prompts"


def test_required_prompt_files_exist():
    for name in [
        "primary_market_scoring.md",
        "primary_market_extraction.md",
        "telegram_brief.md",
    ]:
        path = PROMPTS / name
        assert path.exists(), f"Missing prompt: {name}"
        assert path.read_text(encoding="utf-8").strip()


def test_scoring_prompt_contains_required_schema_fields():
    content = (PROMPTS / "primary_market_scoring.md").read_text(encoding="utf-8")

    for field in [
        "score",
        "final_score",
        "signal_tier",
        "L1",
        "L2",
        "L3",
        "L4",
        "objective_quality",
        "source_type",
        "source_tier",
        "interest_flag",
        "decision_window_status",
        "attribution_chain",
    ]:
        assert field in content


def test_scoring_prompt_declares_score_ranges():
    content = (PROMPTS / "primary_market_scoring.md").read_text(encoding="utf-8")

    assert "final_score" in content
    assert "0-1" in content
    assert "score" in content
    assert "0-10" in content


def test_rimbo_source_scored_v3_declares_source_score_and_compression_contracts():
    scoring = (PROMPTS / "versions" / "rimbo_source_scored_v3" / "scoring.md").read_text(
        encoding="utf-8"
    )
    extraction = (PROMPTS / "versions" / "rimbo_source_scored_v3" / "extraction.md").read_text(
        encoding="utf-8"
    )

    for field in [
        "D1_score",
        "D2_score",
        "D3_score",
        "D4_score",
        "D5_score",
        "L1_score",
        "source_score",
        "content_compression",
        "source_tier",
        "source_type",
        "interest_flag",
        "obsidian_brief_markdown",
    ]:
        assert field in scoring or field in extraction

    assert "Simplified Chinese" in scoring
    assert "Simplified Chinese" in extraction
    assert "## 信源评分" in extraction


def test_v2_stable_cn_declares_v2_style_and_v3_contracts():
    scoring = (PROMPTS / "versions" / "v2_stable_cn" / "scoring.md").read_text(
        encoding="utf-8"
    )
    extraction = (PROMPTS / "versions" / "v2_stable_cn" / "extraction.md").read_text(
        encoding="utf-8"
    )

    assert "商业化/变现实操" in scoring
    assert "默认使用简体中文" in extraction
    assert "source_score" in scoring
    assert "content_compression" in extraction
    assert "obsidian_brief_markdown" in extraction


def test_telegram_prompt_requires_plain_text_and_plain_urls():
    content = (PROMPTS / "telegram_brief.md").read_text(encoding="utf-8")

    assert "plain text" in content
    assert "plain URLs" in content
    assert "Do not use Markdown links" in content
    assert "Do not use Telegram Markdown or HTML formatting" in content
    assert "默认使用简体中文" in content
