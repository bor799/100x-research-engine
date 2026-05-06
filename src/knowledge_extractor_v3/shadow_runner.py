"""Shadow-only real URL runner for Phase 3 validation."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable, Sequence

from .fetchers.base import Fetcher
from .fetchers.web import WebPageFetcher
from .llm.provider import LLMProvider
from .llm.shadow import ShadowHeuristicLLMProvider
from .models import ProcessResult, RuntimeMode, sha256_text, utc_now
from .pipeline import Pipeline
from .prompt_registry import PromptRegistry
from .queue_store import FailureKind, QueueStatus, QueueStore
from .runtime_guard import RuntimeGuard, RuntimeGuardError, RuntimePaths


@dataclass(frozen=True)
class ShadowCandidate:
    id: str
    source_name: str
    source_type: str
    published_at: str
    expected_class: str
    expected_status_hint: str
    title: str
    url: str
    notes: str = ""


@dataclass(frozen=True)
class ShadowRunConfig:
    version: int
    active_prompt_bundle: str
    parallel_test_bundles: list[str]
    defaults: dict[str, object]
    smoke_ids: list[str]
    candidates: list[ShadowCandidate]


@dataclass(frozen=True)
class ShadowRunPaths:
    project_root: Path
    shadow_root: Path
    state_root: Path
    queue_db_path: Path
    staging_obsidian_root: Path
    config_path: Path
    report_path: Path


@dataclass(frozen=True)
class ShadowCandidateResult:
    candidate: ShadowCandidate
    result: ProcessResult
    run_label: str


@dataclass(frozen=True)
class ShadowRunSummary:
    paths: ShadowRunPaths
    results: list[ShadowCandidateResult]
    report_path: Path
    smoke_passed: bool | None


def load_shadow_config(path: Path) -> ShadowRunConfig:
    raw = _load_yaml(path)
    candidates = [
        ShadowCandidate(
            id=str(item.get("id", "")),
            source_name=str(item.get("source_name", "")),
            source_type=str(item.get("source_type", "")),
            published_at=str(item.get("published_at", "")),
            expected_class=str(item.get("expected_class", "")),
            expected_status_hint=str(item.get("expected_status_hint", "")),
            title=str(item.get("title", "")),
            url=str(item.get("url", "")),
            notes=str(item.get("notes", "")),
        )
        for item in raw.get("candidates", [])
    ]
    if not candidates:
        raise ValueError(f"No candidates found in {path}")
    missing = [candidate.id for candidate in candidates if not candidate.id or not candidate.url]
    if missing:
        raise ValueError(f"Candidates must include id and url: {missing}")
    return ShadowRunConfig(
        version=int(raw.get("version", 1)),
        active_prompt_bundle=str(raw.get("active_prompt_bundle", "primary_market_v1")),
        parallel_test_bundles=[str(item) for item in raw.get("parallel_test_bundles", [])],
        defaults=dict(raw.get("defaults", {})),
        smoke_ids=[str(item) for item in raw.get("smoke_ids", [])],
        candidates=candidates,
    )


def select_candidates(
    config: ShadowRunConfig,
    *,
    run_set: str,
    ids: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[ShadowCandidate]:
    by_id = {candidate.id: candidate for candidate in config.candidates}
    if ids:
        selected = []
        for candidate_id in ids:
            try:
                selected.append(by_id[candidate_id])
            except KeyError as exc:
                raise ValueError(f"Unknown candidate id: {candidate_id}") from exc
    elif run_set == "smoke":
        selected = select_candidates(config, run_set="custom", ids=config.smoke_ids)
    elif run_set == "first10-public":
        selected = [
            candidate
            for candidate in config.candidates
            if candidate.source_type != "paywalled_web_article"
        ][:10]
    elif run_set == "all":
        selected = list(config.candidates)
    elif run_set == "custom":
        selected = []
    else:
        raise ValueError(f"Unsupported run set: {run_set}")

    if limit is not None:
        selected = selected[:limit]
    return selected


def run_shadow_candidates(
    *,
    config_path: Path | None = None,
    shadow_root: Path | None = None,
    state_root: Path | None = None,
    queue_db_path: Path | None = None,
    staging_obsidian_root: Path | None = None,
    run_set: str = "smoke",
    ids: Sequence[str] | None = None,
    limit: int | None = None,
    run_parallel_tests: bool = True,
    reject_threshold: float = 0.3,
    fetcher: Fetcher | None = None,
    llm_provider: LLMProvider | None = None,
) -> ShadowRunSummary:
    paths = _resolve_paths(
        config_path=config_path,
        shadow_root=shadow_root,
        state_root=state_root,
        queue_db_path=queue_db_path,
        staging_obsidian_root=staging_obsidian_root,
    )
    _validate_shadow_paths(paths)
    config = load_shadow_config(paths.config_path)

    if run_set == "smoke-then-first10":
        smoke = _run_one_set(
            paths,
            config,
            run_label="smoke",
            candidates=select_candidates(config, run_set="smoke"),
            fetcher=fetcher,
            llm_provider=llm_provider,
            run_parallel_tests=run_parallel_tests,
            reject_threshold=reject_threshold,
        )
        smoke_passed = evaluate_smoke(smoke)
        results = list(smoke)
        if smoke_passed:
            results.extend(
                _run_one_set(
                    paths,
                    config,
                    run_label="first10-public",
                    candidates=select_candidates(config, run_set="first10-public"),
                    fetcher=fetcher,
                    llm_provider=llm_provider,
                    run_parallel_tests=run_parallel_tests,
                    reject_threshold=reject_threshold,
                )
            )
        report_path = write_report(paths, config, results, smoke_passed=smoke_passed)
        return ShadowRunSummary(paths=paths, results=results, report_path=report_path, smoke_passed=smoke_passed)

    selected = select_candidates(config, run_set=run_set, ids=ids, limit=limit)
    results = _run_one_set(
        paths,
        config,
        run_label=run_set,
        candidates=selected,
        fetcher=fetcher,
        llm_provider=llm_provider,
        run_parallel_tests=run_parallel_tests,
        reject_threshold=reject_threshold,
    )
    smoke_passed = evaluate_smoke(results) if run_set == "smoke" else None
    report_path = write_report(paths, config, results, smoke_passed=smoke_passed)
    return ShadowRunSummary(paths=paths, results=results, report_path=report_path, smoke_passed=smoke_passed)


def evaluate_smoke(results: Sequence[ShadowCandidateResult]) -> bool:
    by_id = {item.candidate.id: item.result for item in results}
    scaleops = by_id.get("tc-scaleops-2026-03-30")
    google = by_id.get("tc-google-cloud-next-startups-2026-04-22")
    information = by_id.get("ti-openclaw-2026-04-22")
    if not scaleops or not google or not information:
        return False
    if scaleops.final_status is not QueueStatus.DONE or not scaleops.output_path:
        return False
    if google.final_status is QueueStatus.DONE:
        if google.score_result is None:
            return False
        if google.score_result.final_score >= 0.7 or google.score_result.signal_tier.lower() in {"a", "critical"}:
            return False
    elif google.final_status is not QueueStatus.REJECTED:
        return False
    if information.final_status is QueueStatus.DONE:
        output_path = Path(information.output_path)
        if not _output_has_real_paywall_body(output_path):
            return False
    else:
        if information.failure_kind not in {FailureKind.CONTENT_BLOCKED, FailureKind.AUTH_INVALID, FailureKind.FETCH_FAILED}:
            return False
    return True


def write_report(
    paths: ShadowRunPaths,
    config: ShadowRunConfig,
    results: Sequence[ShadowCandidateResult],
    *,
    smoke_passed: bool | None,
) -> Path:
    paths.shadow_root.mkdir(parents=True, exist_ok=True)
    status_counts = Counter(item.result.final_status.value for item in results)
    output_files = sorted({
        str(Path(item.result.output_path))
        for item in results
        if item.result.output_path and item.result.final_status is QueueStatus.DONE
    })
    failed_or_rejected = [
        item for item in results if item.result.final_status is not QueueStatus.DONE
    ]
    latest_by_id = {item.candidate.id: item for item in results}
    scored = [item for item in latest_by_id.values() if item.result.score_result is not None]
    best = sorted(scored, key=lambda item: item.result.score_result.final_score, reverse=True)[:3]
    worst = sorted(scored, key=lambda item: item.result.score_result.final_score)[:3]

    lines = [
        "# V3 Shadow Run Report",
        "",
        "## Run timestamp and environment paths",
        "",
        f"- Generated at: {utc_now()}",
        f"- Project root: `{paths.project_root}`",
        f"- Shadow root: `{paths.shadow_root}`",
        f"- State root: `{paths.state_root}`",
        f"- Queue DB path: `{paths.queue_db_path}`",
        f"- Staging Obsidian root: `{paths.staging_obsidian_root}`",
        f"- Config path: `{paths.config_path}`",
        f"- Active prompt bundle: `{config.active_prompt_bundle}`",
        f"- Parallel test bundles: `{', '.join(config.parallel_test_bundles) or 'none'}`",
        f"- Smoke passed: `{smoke_passed}`",
        "",
        "## Candidate count and status counts",
        "",
        f"- Attempted candidate executions: {len(results)}",
        f"- Unique candidate IDs: {len({item.candidate.id for item in results})}",
        "",
    ]
    lines.extend(f"- `{status}`: {count}" for status, count in sorted(status_counts.items()))
    lines.extend(["", "## Output file list", ""])
    if output_files:
        lines.extend(f"- `{path}`" for path in output_files)
    else:
        lines.append("- No Obsidian files were written.")
    lines.extend(["", "## Failed/rejected URL table", ""])
    lines.extend(_failure_table(failed_or_rejected))
    lines.extend(["", "## Three best briefs", ""])
    lines.extend(_brief_table(best))
    lines.extend(["", "## Three worst briefs", ""])
    lines.extend(_brief_table(worst))
    lines.extend(["", "## Prompt adjustment notes", ""])
    lines.extend(_prompt_notes(results))
    lines.extend(["", "## Whether to proceed to shadow schedule", ""])
    lines.extend(_proceed_notes(results, smoke_passed=smoke_passed))
    lines.append("")

    paths.report_path.write_text("\n".join(lines), encoding="utf-8")
    _write_jsonl(paths.shadow_root / "results.jsonl", results)
    return paths.report_path


def _run_one_set(
    paths: ShadowRunPaths,
    config: ShadowRunConfig,
    *,
    run_label: str,
    candidates: Sequence[ShadowCandidate],
    fetcher: Fetcher | None,
    llm_provider: LLMProvider | None,
    run_parallel_tests: bool,
    reject_threshold: float,
) -> list[ShadowCandidateResult]:
    fingerprint = _validate_runtime(paths)
    queue_store = QueueStore(
        paths.queue_db_path,
        runtime_fingerprint=sha256_text(fingerprint.to_json(), length=16),
    )
    pipeline = Pipeline(
        queue_store,
        fetcher=fetcher or WebPageFetcher(),
        llm_provider=llm_provider or ShadowHeuristicLLMProvider(),
        prompt_registry=PromptRegistry.default(paths.project_root),
        staging_root=paths.staging_obsidian_root,
        reject_threshold=reject_threshold,
    )
    results: list[ShadowCandidateResult] = []
    for candidate in candidates:
        result = pipeline.process_url(
            candidate.url,
            source=f"{config.defaults.get('source', 'shadow_real_url')}:{candidate.id}",
            mode=RuntimeMode.STAGING,
            prompt_bundle=config.active_prompt_bundle,
            run_parallel_tests=run_parallel_tests,
        )
        wrapped = ShadowCandidateResult(candidate=candidate, result=result, run_label=run_label)
        _assert_output_safety(paths, wrapped)
        results.append(wrapped)
        print(
            json.dumps(
                {
                    "run": run_label,
                    "id": candidate.id,
                    "status": result.final_status.value,
                    "failure_kind": result.failure_kind.value,
                    "output_path": result.output_path,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return results


def _validate_runtime(paths: ShadowRunPaths):
    runtime_paths = RuntimePaths(
        project_root=paths.project_root,
        state_root=paths.state_root,
        queue_db_path=paths.queue_db_path,
        log_path=paths.state_root / "shadow-run.log",
        config_path=paths.config_path,
        home=Path.home(),
    )
    try:
        return RuntimeGuard(runtime_paths).validate(write_fingerprint=True)
    except RuntimeGuardError:
        raise


def _resolve_paths(
    *,
    config_path: Path | None,
    shadow_root: Path | None,
    state_root: Path | None,
    queue_db_path: Path | None,
    staging_obsidian_root: Path | None,
) -> ShadowRunPaths:
    project_root = Path(__file__).resolve().parents[2]
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    effective_shadow_root = Path(
        shadow_root
        or os.environ.get("SHADOW_ROOT", "")
        or Path(tempfile.gettempdir()) / f"100x-v3-shadow-{timestamp}"
    ).expanduser()
    effective_state_root = Path(
        state_root
        or os.environ.get("STATE_ROOT", "")
        or effective_shadow_root / ".100x_v3"
    ).expanduser()
    effective_queue_db = Path(
        queue_db_path
        or os.environ.get("QUEUE_DB_PATH", "")
        or effective_state_root / "queue.db"
    ).expanduser()
    effective_staging_root = Path(
        staging_obsidian_root
        or os.environ.get("STAGING_OBSIDIAN_ROOT", "")
        or effective_shadow_root / "obsidian-staging"
    ).expanduser()
    effective_config = Path(
        config_path
        or project_root / "config" / "shadow-run-url-candidates.example.yaml"
    ).expanduser()
    return ShadowRunPaths(
        project_root=project_root.resolve(strict=False),
        shadow_root=effective_shadow_root.resolve(strict=False),
        state_root=effective_state_root.resolve(strict=False),
        queue_db_path=effective_queue_db.resolve(strict=False),
        staging_obsidian_root=effective_staging_root.resolve(strict=False),
        config_path=effective_config.resolve(strict=False),
        report_path=(effective_shadow_root / "report.md").resolve(strict=False),
    )


def _validate_shadow_paths(paths: ShadowRunPaths) -> None:
    real_queue = (Path.home() / ".100x_v3" / "queue.db").resolve(strict=False)
    if paths.queue_db_path == real_queue:
        raise RuntimeError(f"Refusing to write the default V3 queue during shadow run: {paths.queue_db_path}")
    for label, path in {
        "shadow_root": paths.shadow_root,
        "state_root": paths.state_root,
        "queue_db_path": paths.queue_db_path,
        "staging_obsidian_root": paths.staging_obsidian_root,
        "config_path": paths.config_path,
    }.items():
        text = str(path)
        if ".100x_v2" in text or "/knowledge-extractor/v2" in text:
            raise RuntimeError(f"{label} points at forbidden V2 path: {path}")
    if not _is_relative_to(paths.queue_db_path, paths.state_root):
        raise RuntimeError("QUEUE_DB_PATH must be under STATE_ROOT")
    if not _is_relative_to(paths.state_root, paths.shadow_root):
        raise RuntimeError("STATE_ROOT must be under SHADOW_ROOT for the first shadow run")
    if not _is_relative_to(paths.staging_obsidian_root, paths.shadow_root):
        raise RuntimeError("STAGING_OBSIDIAN_ROOT must be under SHADOW_ROOT")


def _assert_output_safety(paths: ShadowRunPaths, item: ShadowCandidateResult) -> None:
    result = item.result
    if result.final_status is QueueStatus.DONE and not result.output_path:
        raise RuntimeError(f"Done result has no output path: {item.candidate.id}")
    if result.output_path:
        output_path = Path(result.output_path).resolve(strict=False)
        if not _is_relative_to(output_path, paths.staging_obsidian_root):
            raise RuntimeError(f"Output escaped staging root: {output_path}")
    if item.candidate.source_type == "paywalled_web_article" and result.final_status is QueueStatus.DONE:
        if not _output_has_real_paywall_body(Path(result.output_path)):
            raise RuntimeError(f"Paywall candidate was marked done without enough body: {item.candidate.id}")


def _output_has_real_paywall_body(path: Path) -> bool:
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    lower = text.lower()
    if any(marker in lower for marker in ("subscribe to continue", "sign in to continue", "already a subscriber")):
        return False
    return len(text.split()) >= 350


def _failure_table(items: Sequence[ShadowCandidateResult]) -> list[str]:
    if not items:
        return ["No failed or rejected URLs."]
    lines = ["| ID | Status | Failure kind | Next action | URL | Detail |", "|---|---|---|---|---|---|"]
    for item in items:
        result = item.result
        detail = ""
        if result.error:
            detail = result.error.detail or result.error.message
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.candidate.id),
                    _md(result.final_status.value),
                    _md(result.failure_kind.value),
                    _md(result.next_action.value),
                    _md(item.candidate.url),
                    _md(detail),
                ]
            )
            + " |"
        )
    return lines


def _brief_table(items: Sequence[ShadowCandidateResult]) -> list[str]:
    if not items:
        return ["No scored briefs."]
    lines = ["| ID | Status | Final score | Tier | Title | Output |", "|---|---|---:|---|---|---|"]
    for item in items:
        result = item.result
        score = result.score_result
        title = result.extraction_result.title if result.extraction_result else item.candidate.title
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.candidate.id),
                    _md(result.final_status.value),
                    f"{score.final_score:.2f}" if score else "",
                    _md(score.signal_tier if score else ""),
                    _md(title),
                    _md(result.output_path),
                ]
            )
            + " |"
        )
    return lines


def _prompt_notes(results: Sequence[ShadowCandidateResult]) -> list[str]:
    notes = [
        "- The run used the real V3 prompt registry and prompt bundle wiring, but the Phase 3 runner deliberately used a shadow-only heuristic provider because no live LLM provider or secrets are present in V3.",
        "- Replace the heuristic provider with a real provider only after adding explicit V3-only credentials and typed rate-limit handling.",
    ]
    rejected_public = [
        item for item in results
        if item.candidate.source_type != "paywalled_web_article"
        and item.result.final_status is QueueStatus.REJECTED
    ]
    if rejected_public:
        notes.append("- Review rejected public articles manually to tune the future real prompt threshold.")
    blocked = [
        item for item in results
        if item.result.failure_kind in {FailureKind.CONTENT_BLOCKED, FailureKind.AUTH_INVALID}
    ]
    if blocked:
        notes.append("- Keep paywall/auth failures visible as non-done rows; do not add credentialed fetching until a separate V3 auth design exists.")
    return notes


def _proceed_notes(results: Sequence[ShadowCandidateResult], *, smoke_passed: bool | None) -> list[str]:
    if smoke_passed is False:
        return ["Do not proceed to a shadow schedule yet; the smoke gate did not pass."]
    public_failures = [
        item for item in results
        if item.candidate.source_type != "paywalled_web_article"
        and item.result.final_status in {QueueStatus.FAILED_TERMINAL, QueueStatus.RETRY_SCHEDULED}
    ]
    done_without_path = [
        item for item in results
        if item.result.final_status is QueueStatus.DONE and not item.result.output_path
    ]
    if done_without_path:
        return ["Do not proceed; at least one done row lacks an output path."]
    if public_failures:
        return ["Proceed only after reviewing public fetch failures; fetch reliability is not yet good enough for scheduled shadow runs."]
    return ["Proceed to a limited shadow schedule only after a human reviews the generated markdown quality."]


def _write_jsonl(path: Path, results: Sequence[ShadowCandidateResult]) -> None:
    rows = []
    for item in results:
        score = item.result.score_result
        rows.append(
            json.dumps(
                {
                    "run": item.run_label,
                    "id": item.candidate.id,
                    "url": item.candidate.url,
                    "status": item.result.final_status.value,
                    "failure_kind": item.result.failure_kind.value,
                    "next_action": item.result.next_action.value,
                    "output_path": item.result.output_path,
                    "final_score": score.final_score if score else None,
                    "signal_tier": score.signal_tier if score else "",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")


def _load_yaml(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_shadow_yaml_subset(text)
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return loaded


def _parse_shadow_yaml_subset(text: str) -> dict[str, object]:
    data: dict[str, object] = {}
    section = ""
    current_candidate: dict[str, object] | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        stripped = raw_line.strip()
        if indent == 0:
            key, _, raw_value = stripped.partition(":")
            section = key
            if raw_value.strip():
                data[key] = _yaml_scalar(raw_value.strip())
            elif key in {"parallel_test_bundles", "smoke_ids", "candidates"}:
                data[key] = []
            else:
                data[key] = {}
            continue
        if section in {"parallel_test_bundles", "smoke_ids"} and stripped.startswith("- "):
            values = data.setdefault(section, [])
            assert isinstance(values, list)
            values.append(_yaml_scalar(stripped[2:].strip()))
            continue
        if section == "defaults" and indent == 2:
            defaults = data.setdefault("defaults", {})
            assert isinstance(defaults, dict)
            key, _, raw_value = stripped.partition(":")
            defaults[key] = _yaml_scalar(raw_value.strip())
            continue
        if section == "candidates":
            candidates = data.setdefault("candidates", [])
            assert isinstance(candidates, list)
            if indent == 2 and stripped.startswith("- "):
                current_candidate = {}
                candidates.append(current_candidate)
                remainder = stripped[2:].strip()
                if remainder:
                    key, _, raw_value = remainder.partition(":")
                    current_candidate[key] = _yaml_scalar(raw_value.strip())
                continue
            if indent == 4 and current_candidate is not None:
                key, _, raw_value = stripped.partition(":")
                current_candidate[key] = _yaml_scalar(raw_value.strip())
                continue
        raise ValueError(f"Unsupported YAML subset line: {raw_line}")
    return data


def _yaml_scalar(value: str) -> object:
    if not value:
        return ""
    if value[0:1] == value[-1:] == '"':
        return json.loads(value)
    if value[0:1] == value[-1:] == "'":
        return value[1:-1].replace("''", "'")
    if value.isdigit():
        return int(value)
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _is_relative_to(child: Path, parent: Path) -> bool:
    try:
        child.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def _md(value: object) -> str:
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text


def _split_ids(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run V3 Phase 3 shadow-only real URL candidates.")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--shadow-root", type=Path, default=None)
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--queue-db-path", type=Path, default=None)
    parser.add_argument("--staging-obsidian-root", type=Path, default=None)
    parser.add_argument(
        "--run-set",
        choices=("smoke", "first10-public", "all", "smoke-then-first10"),
        default="smoke",
    )
    parser.add_argument("--ids", default="")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-parallel-tests", action="store_true")
    parser.add_argument("--reject-threshold", type=float, default=0.3)
    args = parser.parse_args(argv)

    summary = run_shadow_candidates(
        config_path=args.config,
        shadow_root=args.shadow_root,
        state_root=args.state_root,
        queue_db_path=args.queue_db_path,
        staging_obsidian_root=args.staging_obsidian_root,
        run_set=args.run_set,
        ids=_split_ids(args.ids),
        limit=args.limit,
        run_parallel_tests=not args.no_parallel_tests,
        reject_threshold=args.reject_threshold,
    )
    print(f"report_path={summary.report_path}")
    if summary.smoke_passed is False:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
