from pathlib import Path

from knowledge_extractor_v3.fetchers.fixture import FixtureFetcher
from knowledge_extractor_v3.shadow_runner import (
    load_shadow_config,
    run_shadow_candidates,
    select_candidates,
)


def test_shadow_config_parser_reads_example_candidates():
    config = load_shadow_config(Path("config/shadow-run-url-candidates.example.yaml"))

    assert config.active_prompt_bundle == "primary_market_v1"
    assert config.smoke_ids == [
        "tc-scaleops-2026-03-30",
        "tc-google-cloud-next-startups-2026-04-22",
        "ti-openclaw-2026-04-22",
    ]
    assert len(config.candidates) == 23


def test_select_first10_public_excludes_paywalled_candidates():
    config = load_shadow_config(Path("config/shadow-run-url-candidates.example.yaml"))

    selected = select_candidates(config, run_set="first10-public")

    assert len(selected) == 10
    assert all(candidate.source_type != "paywalled_web_article" for candidate in selected)


def test_shadow_runner_uses_temp_state_and_writes_report(tmp_path):
    config_path = tmp_path / "shadow.yaml"
    config_path.write_text(
        """
version: 1
active_prompt_bundle: primary_market_v1
parallel_test_bundles:
  - v2_legacy
defaults:
  source: shadow_real_url
smoke_ids:
  - fixture-high
candidates:
  - id: fixture-high
    source_name: Fixture
    source_type: web_article
    published_at: "2026-04-28"
    expected_class: high_signal
    expected_status_hint: done
    title: "Fixture High"
    url: "fixture://high_signal"
    notes: "test"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    shadow_root = tmp_path / "shadow"

    summary = run_shadow_candidates(
        config_path=config_path,
        shadow_root=shadow_root,
        run_set="smoke",
        fetcher=FixtureFetcher(),
        run_parallel_tests=False,
    )

    assert summary.paths.queue_db_path == shadow_root / ".100x_v3" / "queue.db"
    assert summary.report_path == shadow_root / "report.md"
    assert summary.report_path.exists()
    assert (shadow_root / "obsidian-staging" / "obsidian").exists()
