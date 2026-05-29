#!/usr/bin/env python3
"""Manual URL testing script for V3.

Usage:
    python scripts/test_url.py <url> [--mode dry_run|staging|live]
"""

import os
import sys
from pathlib import Path

# Add src to path
project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))

from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.fetchers.web import WebPageFetcher
from knowledge_extractor_v3.fetchers.router import FetcherRouter
from knowledge_extractor_v3.llm.shadow import ShadowHeuristicLLMProvider
from knowledge_extractor_v3.llm.live_provider import create_live_provider
from knowledge_extractor_v3.models import RuntimeMode
from knowledge_extractor_v3.outputs.obsidian import DryRunOutputPort, StagingOutputPort
from knowledge_extractor_v3.outputs.live_obsidian import LiveOutputPort, LiveObsidianWriter
from knowledge_extractor_v3.outputs.telegram_live import LiveTelegramClient
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.prompt_registry import PromptRegistry
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.runtime_guard import RuntimeGuard, RuntimeGuardError


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Test V3 with a single URL")
    parser.add_argument("url", help="URL to process")
    parser.add_argument(
        "--mode", "-m",
        choices=["dry_run", "staging", "live"],
        default="dry_run",
        help="Runtime mode (default: dry_run)",
    )
    parser.add_argument(
        "--staging-root",
        type=Path,
        default=None,
        help="Staging output root (default: temp directory)",
    )
    parser.add_argument(
        "--use-shadow-llm",
        action="store_true",
        help="Use shadow heuristic LLM instead of real API",
    )
    parser.add_argument(
        "--prompt-bundle",
        default=None,
        help="Prompt bundle to use for this run (default: config/registry active bundle)",
    )
    parser.add_argument(
        "--run-parallel-prompts",
        action="store_true",
        help="Also run configured parallel prompt bundles for comparison",
    )
    parser.add_argument(
        "--allow-test-provider",
        action="store_true",
        help="Allow test providers (stub/shadow) for non-fixture URLs",
    )

    args = parser.parse_args()

    mode = RuntimeMode(args.mode)

    # Load config
    loader = ConfigLoader(project_root=project_root)
    config = loader.load()

    print(f"V3 URL Test - Mode: {mode.value}")
    print(f"URL: {args.url}")
    print()

    # Runtime guard check
    if mode is RuntimeMode.LIVE:
        guard = RuntimeGuard.from_env(project_root=project_root)
        try:
            guard.validate(write_fingerprint=False)
            print("✓ Runtime guard check passed")
        except RuntimeGuardError as exc:
            print(f"✗ Runtime guard check failed: {exc}")
            return 1

    # Create fetcher
    fetcher = FetcherRouter()

    # Create LLM provider
    if args.use_shadow_llm or mode is not RuntimeMode.LIVE:
        print("Using ShadowHeuristicLLMProvider (no real API calls)")
        llm = ShadowHeuristicLLMProvider()
    else:
        print(f"Using live LLM provider: {config.llm.provider}")
        llm = create_live_provider(config.llm, env=os.environ)

    # Create prompt registry
    prompts = PromptRegistry.from_config(project_root, config.prompts)
    active_prompt_bundle = args.prompt_bundle or prompts.active_bundle_name
    print(f"Active prompt bundle: {active_prompt_bundle}")
    if args.run_parallel_prompts:
        print(f"Parallel prompt bundles: {', '.join(prompts.parallel_test_bundle_names) or 'none'}")

    # Create output port
    if mode is RuntimeMode.DRY_RUN:
        output = DryRunOutputPort()
        print("Output mode: DRY_RUN (no files written)")
    elif mode is RuntimeMode.STAGING:
        staging_root = args.staging_root or Path(project_root / ".test-staging")
        staging_root.mkdir(parents=True, exist_ok=True)
        output = StagingOutputPort(staging_root)
        print(f"Output mode: STAGING -> {staging_root}")
    else:  # LIVE
        obsidian_root = loader.expand_path(config.outputs.obsidian_root)
        writer = LiveObsidianWriter(
            root=obsidian_root,
            subdir=config.outputs.obsidian_subdir,
            write_manifest=config.outputs.write_manifest,
        )

        telegram = None
        if config.outputs.telegram_enabled:
            token = loader.resolve_env(config.outputs.telegram_bot_token_env)
            chat_id = loader.resolve_env(config.outputs.telegram_admin_chat_id_env)
            if token and chat_id:
                telegram = LiveTelegramClient(
                    bot_token=token,
                    chat_id=chat_id,
                    enabled=True,
                )
                print(f"Telegram output enabled: chat_id={chat_id}")

        output = LiveOutputPort(obsidian_writer=writer, telegram_client=telegram)
        print(f"Output mode: LIVE -> {obsidian_root / config.outputs.obsidian_subdir}")

    # Create queue store (in-memory for testing)
    import tempfile
    queue_db = Path(tempfile.mktemp(suffix=".db"))
    queue = QueueStore(queue_db)

    # Create pipeline
    pipeline = Pipeline(
        queue_store=queue,
        fetcher=fetcher,
        llm_provider=llm,
        prompt_registry=prompts,
        staging_root=staging_root if mode is RuntimeMode.STAGING else None,
        live_output=output if mode is RuntimeMode.LIVE else None,
        allow_test_provider=args.allow_test_provider,
    )

    if args.allow_test_provider:
        provider_route = str(getattr(llm, "model_route", ""))
        print(f"WARNING: Test provider authorized ({provider_route})")

    print()
    print("Processing...")
    print("-" * 60)

    # Process URL
    result = pipeline.process_url(
        args.url,
        mode=mode,
        prompt_bundle=args.prompt_bundle,
        run_parallel_tests=args.run_parallel_prompts,
    )

    # Display results
    print()
    print("=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Status: {result.final_status.value}")
    print(f"Stage: {result.current_stage}")

    if result.error:
        print(f"Error: {result.error.message}")
        print(f"Failure kind: {result.error.failure_kind.value}")

    if result.score_result:
        score = result.score_result
        print()
        print("Scoring:")
        print(f"  Score: {score.score:.2f} / 10")
        print(f"  Final score: {score.final_score:.2f}")
        print(f"  Signal tier: {score.signal_tier}")

    if result.extraction_result:
        ext = result.extraction_result
        print()
        print("Extraction:")
        print(f"  Title: {ext.title}")
        print(f"  Signal: {ext.one_line_signal[:100]}...")
        if "source_score" in ext.parsed:
            source_score = ext.parsed["source_score"]
            print(f"  Source score: {source_score}")
        if "content_compression" in ext.parsed:
            compression = ext.parsed["content_compression"]
            print(f"  Compression: {compression}")

    if result.parallel_results:
        print()
        print("Parallel prompts:")
        for item in result.parallel_results:
            status = "ok" if item.ok else "failed"
            score = item.score_result.final_score if item.score_result else ""
            print(f"  {item.prompt_bundle}: {status} {score}")

    print()
    print("Stages:")
    for stage in result.stage_results:
        status = "✓" if stage.ok else "✗"
        duration = f"{stage.duration_ms}ms" if stage.duration_ms > 0 else ""
        print(f"  {status} {stage.stage:20s} {duration}")

    if result.output_path:
        print()
        print(f"Output: {result.output_path}")

    if result.telegram_status:
        print(f"Telegram: {result.telegram_status}")

    # Cleanup
    try:
        queue_db.unlink(missing_ok=True)
    except Exception:
        pass

    return 0 if result.final_status.value in ("done", "rejected") else 1


if __name__ == "__main__":
    sys.exit(main())
