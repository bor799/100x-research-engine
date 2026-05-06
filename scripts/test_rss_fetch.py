#!/usr/bin/env python3
"""RSS feed smoke test for the active V3 source registry.

This script intentionally reads the same V3 config as the scheduler, instead of
hard-coding old URLs that may no longer be part of production.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PYTHON_MIN = (3, 11)
SUPPORTED_PYTHON_MAX = (3, 14)


def _python_supported(executable: str) -> bool:
    probe = (
        "import sys; "
        "raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 14) else 1)"
    )
    try:
        return subprocess.run(
            [executable, "-c", probe],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode == 0
    except OSError:
        return False


def _ensure_supported_python() -> None:
    current_version = sys.version_info[:2]
    if SUPPORTED_PYTHON_MIN <= current_version < SUPPORTED_PYTHON_MAX:
        return

    for candidate in (
        os.environ.get("PYTHON"),
        str(PROJECT_ROOT / ".venv" / "bin" / "python"),
        "python",
        "python3.13",
        "python3.12",
        "python3.11",
        "/opt/miniconda3/bin/python",
    ):
        if not candidate:
            continue
        resolved = shutil.which(candidate) if "/" not in candidate else candidate
        if (
            resolved
            and Path(resolved).exists()
            and Path(resolved).resolve() != Path(sys.executable).resolve()
            and _python_supported(resolved)
        ):
            os.execv(resolved, [resolved, *sys.argv])

    raise SystemExit("Python >= 3.11,<3.14 is required. Run with the project Python.")


_ensure_supported_python()
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.sources.models import SourceConfig
from knowledge_extractor_v3.sources.rss import RSSAdapter


def main() -> int:
    parser = argparse.ArgumentParser(description="Test current V3 RSS source fetch/parse success rate.")
    parser.add_argument("--limit", type=int, default=10, help="Number of RSS sources to test, ignored by --all")
    parser.add_argument("--all", action="store_true", help="Test all configured RSS sources")
    parser.add_argument("--timeout", type=int, default=20, help="Per-feed timeout in seconds")
    parser.add_argument("--lookback-days", type=int, default=3650, help="Wide lookback for feed validity checks")
    parser.add_argument("--target-rate", type=float, default=95.0, help="Required success rate percentage")
    parser.add_argument("--json", action="store_true", help="Emit JSON report")
    args = parser.parse_args()

    loader = ConfigLoader(project_root=Path.cwd())
    config = loader.load()
    sources = [_to_source_config(item) for item in config.sources if item.enabled and item.type == "rss"]
    selected = sources if args.all else sources[: args.limit]

    adapter = RSSAdapter(timeout=args.timeout)
    results = []
    started = time.perf_counter()

    for source in selected:
        item_started = time.perf_counter()
        try:
            items = adapter.discover(source, lookback_days=args.lookback_days)
            ok = len(items) > 0
            results.append({
                "source": source.id,
                "url": source.url,
                "ok": ok,
                "items": len(items),
                "duration_ms": int((time.perf_counter() - item_started) * 1000),
                "error": "" if ok else "no_items_or_parse_failed",
            })
        except Exception as exc:  # noqa: BLE001 - smoke report should keep going.
            results.append({
                "source": source.id,
                "url": source.url,
                "ok": False,
                "items": 0,
                "duration_ms": int((time.perf_counter() - item_started) * 1000),
                "error": f"{type(exc).__name__}: {exc}",
            })

    success = sum(1 for item in results if item["ok"])
    total = len(results)
    rate = (success / total * 100.0) if total else 0.0
    report = {
        "config": str(loader.config_path_used),
        "sources_loaded": len(config.sources),
        "rss_sources_loaded": len(sources),
        "tested": total,
        "success": success,
        "success_rate": round(rate, 2),
        "target_rate": args.target_rate,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "results": results,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)

    return 0 if rate >= args.target_rate else 1


def _to_source_config(item) -> SourceConfig:
    metadata = dict(item.metadata)
    if item.category:
        metadata.setdefault("category", item.category)
    if item.cron_interval:
        metadata.setdefault("cron_interval", item.cron_interval)
    return SourceConfig(
        id=item.name,
        type=item.type,
        url=item.url,
        enabled=item.enabled,
        priority=item.priority,
        tags=item.tags,
        max_items=item.max_items,
        lookback_days=item.lookback_days,
        metadata=metadata,
    )


def _print_report(report: dict[str, object]) -> None:
    print("RSS Fetch Success Rate Test")
    print("=" * 60)
    print(f"Config: {report['config']}")
    print(f"Sources loaded: {report['sources_loaded']} ({report['rss_sources_loaded']} RSS)")
    print(f"Tested: {report['tested']}")
    print(f"Success: {report['success']} ({report['success_rate']}%)")
    print(f"Target: {report['target_rate']}%")
    print()
    failed = [item for item in report["results"] if not item["ok"]]
    if failed:
        print("Failures:")
        for item in failed:
            print(f"  - {item['source']}: {item['error']} ({item['url']})")
    else:
        print("All tested feeds returned parseable items.")


if __name__ == "__main__":
    raise SystemExit(main())
