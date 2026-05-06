"""Fixture-backed fetcher for Phase 2 dry-run and staging tests."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from ..models import FetchedContent, TypedError, sha256_text, utc_now
from ..queue_store import FailureKind, NextAction


DEFAULT_FIXTURE_FILES = {
    "high_signal": "high_signal_primary_market.md",
    "low_quality": "low_quality_marketing.md",
    "parse_error": "parse_error_candidate.md",
    "llm_rate_limit": "llm_rate_limit_candidate.md",
    "llm_timeout": "llm_timeout_candidate.md",
    "fetch_failed": "fetch_failed_candidate.md",
    "output_failed": "output_failed_candidate.md",
}


class FixtureFetcher:
    """Fetch article fixtures without touching HOME, V2 state, or the network."""

    def __init__(
        self,
        fixtures_root: Path | None = None,
        *,
        fixture_map: Mapping[str, Path | str] | None = None,
    ) -> None:
        project_root = Path(__file__).resolve().parents[3]
        self.fixtures_root = Path(fixtures_root or project_root / "tests" / "fixtures" / "articles")
        self.fixture_map = dict(fixture_map or {})

    def fetch(self, url: str) -> FetchedContent | TypedError:
        scenario = self._scenario(url)
        if scenario == "fetch_failed":
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message="Fixture fetch failed",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=url,
            )

        path = self._fixture_path(url, scenario)
        if path is None or not path.exists():
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message="Fixture article not found",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=str(path or url),
            )

        text = path.read_text(encoding="utf-8").strip()
        title = _title_from_markdown(text, fallback=path.stem.replace("_", " ").title())
        content_hash = sha256_text(text)
        return FetchedContent(
            url=url,
            source="fixture",
            source_type="fixture",
            title=title,
            text=text,
            raw=text,
            fetched_at=utc_now(),
            content_hash=content_hash,
            metadata={
                "fixture_scenario": scenario,
                "fixture_path": str(path),
            },
        )

    def _fixture_path(self, url: str, scenario: str) -> Path | None:
        raw = self.fixture_map.get(url) or self.fixture_map.get(scenario)
        if raw is not None:
            return Path(raw)

        filename = DEFAULT_FIXTURE_FILES.get(scenario)
        if filename:
            return self.fixtures_root / filename
        return None

    @staticmethod
    def _scenario(url: str) -> str:
        if url.startswith("fixture://"):
            return url.removeprefix("fixture://").strip("/") or "high_signal"
        return "high_signal"


def _title_from_markdown(text: str, *, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip() or fallback
    return fallback
