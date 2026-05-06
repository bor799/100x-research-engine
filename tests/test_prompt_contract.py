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


def test_telegram_prompt_requires_plain_text_and_plain_urls():
    content = (PROMPTS / "telegram_brief.md").read_text(encoding="utf-8")

    assert "plain text" in content
    assert "plain URLs" in content
    assert "Do not use Markdown links" in content
    assert "Do not use Telegram Markdown or HTML formatting" in content
