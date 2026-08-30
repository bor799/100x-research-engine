"""Pipeline-level dedup early exit: duplicates and same-URL updates never
reach the absorption LLM call.

The pipeline seam is the VaultDedupService protocol (lookup + merge_update);
these tests pin the contract with a fake so the queue/LLM behaviour stays
observable: score_calls == 0, the task lands DONE pointing at the canonical
file, TypedErrors keep their retry semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

from knowledge_extractor_v3.llm.provider import StubLLMProvider
from knowledge_extractor_v3.models import RuntimeMode
from knowledge_extractor_v3.outputs.live_obsidian import LiveObsidianWriter, LiveOutputPort
from knowledge_extractor_v3.outputs.updates import UpdateOutcome
from knowledge_extractor_v3.outputs.vault_index import DedupLookup, VaultArticleRef
from knowledge_extractor_v3.pipeline import Pipeline
from knowledge_extractor_v3.queue_store import FailureKind, NextAction, QueueStatus, QueueStore
from knowledge_extractor_v3.models import TypedError

WEEK = "2026-08-W4"
URL = "https://example.com/article"


class CountingAbsorption(StubLLMProvider):
    """One LLM call shape + a loud counter for the dedup assertions."""

    model_route = "test://counting-absorption"

    def __init__(self) -> None:
        self.score_calls = 0

    def score(self, content, prompt: str) -> str:
        self.score_calls += 1
        return json.dumps(
            {
                "information_gain": 0.8, "action_value": 0.8, "relevance": 0.8,
                "is_spam": False, "rationale": "r",
                "title": "常规吸收结果", "one_line_summary": "一句话",
                "category": "技术创业", "experiences": ["e"], "signals": ["s"],
                "key_facts": ["f"], "quote": "", "next_action": "n",
                "obsidian_brief_markdown": "# 存档",
            },
            ensure_ascii=False,
        )


class FakeVaultDedup:
    def __init__(self, *, by_hash: VaultArticleRef | None = None,
                 by_url: VaultArticleRef | None = None,
                 merge_result=None) -> None:
        self._by_hash = by_hash
        self._by_url = by_url
        self._merge_result = merge_result
        self.merge_calls: list[tuple[VaultArticleRef, object]] = []

    def lookup(self, *, content_hash: str, url: str) -> DedupLookup:
        return DedupLookup(by_hash=self._by_hash, by_url=self._by_url)

    def merge_update(self, ref, fetched):
        self.merge_calls.append((ref, fetched))
        return self._merge_result


def _canonical(tmp_path: Path) -> VaultArticleRef:
    path = tmp_path / WEEK / "canonical.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\ntype: knowledge-extract\narticle_id: oldhash\nurl: " + URL + "\ntitle: 已归档标题\n---\n正文",
        encoding="utf-8",
    )
    return VaultArticleRef(
        article_id="oldhash", path=path, week=WEEK, url=URL, title="已归档标题",
    )


def _pipeline(tmp_path: Path, dedup, llm: CountingAbsorption | None = None) -> Pipeline:
    store = QueueStore(tmp_path / "queue.db", runtime_fingerprint="test-fp")
    writer = LiveOutputPort(
        obsidian_writer=LiveObsidianWriter(tmp_path / "vault", write_manifest=False)
    )
    return Pipeline(
        store,
        llm_provider=llm or CountingAbsorption(),
        live_output=writer,
        staging_root=tmp_path / "staging",
        allow_test_provider=True,
        vault_dedup=dedup,
    )


def test_duplicate_hash_marks_done_with_zero_llm_calls(tmp_path):
    canonical = _canonical(tmp_path)
    llm = CountingAbsorption()
    dedup = FakeVaultDedup(by_hash=canonical)
    pipeline = _pipeline(tmp_path, dedup, llm)

    result = pipeline.process_url(URL, source="manual", mode=RuntimeMode.LIVE)

    assert result.final_status is QueueStatus.DONE
    assert result.dedup_outcome == "duplicate_hash"
    assert result.output_path == str(canonical.path)
    assert llm.score_calls == 0
    row = pipeline.queue_store.find_by_url(URL)
    assert row is not None and row.status is QueueStatus.DONE
    assert row.output_path == str(canonical.path)
    # The stub's default complete() reports no-update; it must never run either.
    assert dedup.merge_calls == []


def test_same_url_update_merges_with_zero_llm_calls(tmp_path):
    canonical = _canonical(tmp_path)
    llm = CountingAbsorption()
    dedup = FakeVaultDedup(
        by_url=canonical,
        merge_result=UpdateOutcome(kind="merged", path=str(canonical.path), entry={}),
    )
    pipeline = _pipeline(tmp_path, dedup, llm)

    result = pipeline.process_url(URL, source="manual", mode=RuntimeMode.LIVE)

    assert result.final_status is QueueStatus.DONE
    assert result.dedup_outcome == "merged_update"
    assert llm.score_calls == 0
    assert len(dedup.merge_calls) == 1
    stage_names = [s.stage for s in result.stage_results]
    assert "dedup_check" in stage_names and "increment" in stage_names


def test_merge_typed_error_keeps_retry_scheduled(tmp_path):
    canonical = _canonical(tmp_path)
    dedup = FakeVaultDedup(
        by_url=canonical,
        merge_result=TypedError(
            failure_kind=FailureKind.LLM_RATE_LIMIT,
            message="provider throttled", stage="increment",
            retryable=True, next_action=NextAction.RETRY_LATER,
        ),
    )
    pipeline = _pipeline(tmp_path, dedup)

    result = pipeline.process_url(URL, source="manual", mode=RuntimeMode.LIVE)

    assert result.final_status is QueueStatus.RETRY_SCHEDULED
    assert result.failure_kind is FailureKind.LLM_RATE_LIMIT
    row = pipeline.queue_store.find_by_url(URL)
    assert row is not None and row.status is QueueStatus.RETRY_SCHEDULED


def test_none_service_keeps_baseline_stage_set(tmp_path):
    llm = CountingAbsorption()
    pipeline = _pipeline(tmp_path, None, llm)

    result = pipeline.process_url(URL, source="manual", mode=RuntimeMode.LIVE)

    stage_names = {s.stage for s in result.stage_results}
    assert "dedup_check" not in stage_names and "increment" not in stage_names
    assert llm.score_calls == 1
    assert result.final_status is QueueStatus.DONE
    assert result.dedup_outcome == ""
