#!/usr/bin/env python3
"""Run a bounded V2/V3 shadow comparison for selected URLs."""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

from knowledge_extractor_v3.fetchers.router import FetcherRouter
from knowledge_extractor_v3.models import RuntimeMode, TypedError
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.queue_store import QueueStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare V2 dry-run with V3 staging/dry-run.")
    parser.add_argument("urls", nargs="*", help="URLs to compare")
    parser.add_argument("--url-file", help="Text file with one URL per line")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    urls = list(args.urls)
    if args.url_file:
        urls.extend(_load_urls(Path(args.url_file)))
    urls = [u for u in dict.fromkeys(urls) if u][: args.limit]
    if not urls:
        parser.error("Provide at least one URL or --url-file")

    report = {
        "generated_at": int(time.time()),
        "v2_root": str(_v2_root()),
        "items": [_compare_url(url, args.timeout) for url in urls],
    }
    report["summary"] = {
        "total": len(report["items"]),
        "v2_ok": sum(1 for item in report["items"] if item["v2"]["ok"]),
        "v3_fetch_ok": sum(1 for item in report["items"] if item["v3_fetch"]["ok"]),
        "v3_pipeline_ok": sum(1 for item in report["items"] if item["v3_pipeline"]["ok"]),
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0 if report["summary"]["v3_pipeline_ok"] == report["summary"]["total"] else 1


def _load_urls(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _compare_url(url: str, timeout: int) -> dict[str, object]:
    return {
        "url": url,
        "v2": _run_v2(url, timeout),
        "v3_fetch": _run_v3_fetch(url),
        "v3_pipeline": _run_v3_pipeline(url),
    }


def _run_v2(url: str, timeout: int) -> dict[str, object]:
    root = _v2_root()
    if not root.exists():
        return {"ok": False, "status": "missing_v2", "detail": str(root)}
    python = root / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python3")
    cmd = [str(python), "-m", "src.main", "--dry-run", "--url", url]
    try:
        completed = subprocess.run(
            cmd,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "timeout", "detail": f">{timeout}s"}
    except OSError as exc:
        return {"ok": False, "status": "error", "detail": str(exc)}
    return {
        "ok": completed.returncode == 0,
        "status": "ok" if completed.returncode == 0 else "failed",
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-800:],
        "stderr_tail": completed.stderr[-800:],
    }


def _run_v3_fetch(url: str) -> dict[str, object]:
    result = FetcherRouter().fetch(url)
    if isinstance(result, TypedError):
        return {
            "ok": False,
            "status": result.failure_kind.value,
            "detail": result.message,
            "next_action": result.next_action.value,
        }
    return {
        "ok": True,
        "status": "ok",
        "title": result.title,
        "source_type": result.source_type,
        "word_count": len(result.text.split()),
        "fetcher": result.metadata.get("fetcher", ""),
    }


def _run_v3_pipeline(url: str) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="100x-v3-compare-") as tmp:
        tmp_path = Path(tmp)
        store = QueueStore(tmp_path / "queue.db", runtime_fingerprint="v2-compare")
        pipeline = Pipeline(
            store,
            fetcher=FetcherRouter(),
            staging_root=tmp_path / "staging",
        )
        result = pipeline.process_url(url, source="v2_compare", mode=RuntimeMode.STAGING)
        return {
            "ok": result.final_status.value == "done",
            "status": result.final_status.value,
            "failure_kind": result.failure_kind.value,
            "next_action": result.next_action.value,
            "output_path": result.output_path,
            "stage": result.current_stage,
        }


def _v2_root() -> Path:
    return Path(__file__).resolve().parents[2] / "v2"


def _print_report(report: dict[str, object]) -> None:
    summary = report["summary"]
    assert isinstance(summary, dict)
    print("V2/V3 shadow comparison")
    print(f"Total: {summary['total']} | V2 ok: {summary['v2_ok']} | V3 fetch ok: {summary['v3_fetch_ok']} | V3 pipeline ok: {summary['v3_pipeline_ok']}")
    print()
    for item in report["items"]:
        assert isinstance(item, dict)
        print(item["url"])
        print(f"  V2: {item['v2']['status']}")
        print(f"  V3 fetch: {item['v3_fetch']['status']}")
        print(f"  V3 pipeline: {item['v3_pipeline']['status']} ({item['v3_pipeline']['failure_kind']})")


if __name__ == "__main__":
    raise SystemExit(main())
