# 100X Knowledge Extractor V3 Architecture

V3 is a clean sibling repository of V2, built for primary-market investment intelligence. It does not patch V2 in place and does not share V2 runtime state.

## Goals

V3 optimizes for investment signals that are still inside a decision window:

- information asymmetry
- source credibility
- claim provenance
- operating traces
- conflict-of-interest labeling
- evidence-backed Obsidian briefs
- short Telegram briefs after the output loop closes

The scoring contract is:

```text
objective_quality = L1 * L2 * L3
final_score = 0.70 * objective_quality + 0.30 * L4
score = final_score * 10
```

`final_score` is always `0-1`; `score` is always `0-10` for compatibility with threshold-style routing.

## Phase 1 Scope

Phase 1 includes only:

- repository skeleton
- docs and prompt contracts
- `RuntimeGuard` interface
- `QueueStore` interface and minimal SQLite contract
- tests for runtime isolation, queue semantics, and prompt contract

Phase 1 explicitly excludes:

- live daemon
- scheduler
- Telegram bot polling
- RSS ingestion
- fetcher implementation
- LLM provider implementation
- Obsidian or Telegram live output
- reading or migrating V2 queue data

## Runtime Isolation

Default V3 runtime paths:

```text
STATE_ROOT=~/.100x_v3
QUEUE_DB_PATH=~/.100x_v3/queue.db
LOG_PATH=~/100x-v3-daemon.log
```

Runtime Guard must run before any future daemon, scheduler, bot, or queue worker. It fails loudly when:

- project root points to `knowledge-extractor/v2`
- state root or queue path contains `.100x_v2`
- HOME contains a known shadow marker such as `codepilot-shadow`
- `QUEUE_DB_PATH` is outside `STATE_ROOT`
- an existing queue database lacks required V3 schema columns

The guard can build a runtime fingerprint containing project root, Python executable, config path, queue DB path, state root, log path, prompt hashes, and source hash. Future live roles must write this fingerprint before processing work.

## Data Flow

The intended full V3 flow is:

```text
URL/RSS/IM input
  -> QueueStore
  -> RuntimeGuard
  -> Fetch
  -> Validate
  -> Score
  -> Analyze
  -> Write Obsidian
  -> Push Telegram
  -> Mark Queue
```

In Phase 1 only `QueueStore` and `RuntimeGuard` exist. Pipeline, fetchers, LLM providers, and outputs are deferred.

## Module Boundaries

- `runtime_guard.py`: validates path isolation, queue schema compatibility, and runtime fingerprint data.
- `queue_store.py`: owns queue schema, statuses, typed failures, retry scheduling, and terminal state semantics.
- `prompts/primary_market_scoring.md`: produces the scoring JSON contract.
- `prompts/primary_market_extraction.md`: produces the investor brief JSON contract.
- `prompts/telegram_brief.md`: produces plain-text Telegram brief content from structured extraction output.

No module may infer defaults from V2 config or V2 state.

## Queue Contract

Statuses are fixed:

```text
pending
processing
retry_scheduled
done
rejected
failed_terminal
```

Core rules:

- Fetch, LLM, parse, or output failure cannot become `done`.
- `done` requires evidence that the output loop closed, represented in Phase 1 by `output_path`.
- Rate limits and timeouts use `retry_scheduled` with `next_retry_at`.
- Bad fit or safety rejection uses `rejected`.
- Exhausted attempts or manual-only failures use `failed_terminal`.
- All failures carry `failure_kind`, `last_error`, and `next_action`.

## Prompt Contract

V3 prompt paths are versioned through:

```text
prompts/registry.json
```

Prompt roles:

- `scoring`: information screening standard.
- `extraction`: information extraction format.
- `telegram_brief`: downstream delivery formatting.

The active bundle is `v2_stable_cn` (production-oriented, Chinese-first). `primary_market_v1` is the V3 primary-market investor prompt set. `v2_legacy` preserves the unmodified V2 baseline for comparison. `rimbo_source_scored_v3` adds source-tier/D1-D5 scoring plus content compression. Pipeline code must load prompts through `PromptRegistry` so multiple bundles can run against the same fetched content during offline evaluation.

The scoring prompt must output JSON containing:

```json
{
  "score": 8.4,
  "final_score": 0.84,
  "signal_tier": "Critical",
  "L1": 0.95,
  "L2": 1.0,
  "L3": 0.87,
  "L4": 0.89,
  "objective_quality": 0.83,
  "decision_window_status": "open",
  "source_type": "LegalDoc",
  "source_tier": "Primary",
  "interest_flag": "Independent",
  "attribution_chain": "registry filing -> PDF extraction -> Fact#2 -> Signal"
}
```

Missing required fields must become `parse_error` in the future parser and must not flow into outputs.

## Configuration

Tracked config files are examples only. Live secrets belong in untracked local files or environment variables.

Never copy V2 `config/config.yaml`, `.env`, queue databases, logs, or `.venv` into V3.
