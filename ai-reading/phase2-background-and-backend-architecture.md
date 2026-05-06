# Phase 2 Background And Backend Architecture

## Context

V3 is a clean sibling repository, not a patch on V2. V2 already has a working chain, but its history shows repeated failure modes: silent daemon stops, queue tasks marked done after failure, shadow HOME queues, LLM rate-limit storms, and vague Telegram failure messages.

V3's job is to build the backend core so a primary-market investor can test whether the new screening and extraction prompts produce better briefs than the V2 baseline.

The user wants to see real effect: scheduled ingestion, Obsidian writing, and Telegram push. That is the live acceptance target, but it must be reached through dry-run and staging gates first.

## Product Goal

Build a backend that can process one URL or scheduled source into an investment brief:

```text
Input URL or scheduled source
  -> Runtime Guard
  -> Queue Store
  -> Fetch
  -> Validate
  -> Prompt Registry
  -> Score
  -> Extract
  -> Write Obsidian
  -> Push Telegram
  -> Mark Queue
```

Every stage must produce structured status. No stage may fail silently.

## Core Concepts

### Prompt Bundle

A prompt bundle chooses which scoring and extraction prompts are used. Multiple bundles can run against the same content for comparison.

Minimum run metadata:

- `prompt_bundle`
- `scoring_prompt_hash`
- `extraction_prompt_hash`
- `model_route`
- `input_hash`

### Process Result

Pipeline must return `ProcessResult`, not `None`.

It should include:

- URL
- source
- queue task id
- current stage
- final status
- retryable flag
- failure kind
- next action
- output path
- Telegram push status
- prompt bundle
- stage durations

### Runtime Guard

Runtime Guard remains mandatory before any live role starts. It prevents:

- V2 queue usage
- V2 project root usage
- shadow HOME usage
- queue schema mismatch
- queue path outside V3 state root

## Backend Modules

### `models.py`

Define:

- `FetchedContent`
- `TypedError`
- `StageResult`
- `ScoreResult`
- `ExtractionResult`
- `ProcessResult`
- `OutputResult`

### `prompt_registry.py`

Already started in Phase 1. Phase 2 should integrate it into LLM calls and run metadata.

### `prompt_parser.py`

Responsibilities:

- parse JSON from scoring and extraction outputs
- validate required fields
- validate `final_score` range `0-1`
- validate `score` range `0-10`
- turn missing or invalid fields into `FailureKind.PARSE_ERROR`

### `llm/provider.py`

Start with stub provider and model route interface:

- `score(content, prompt)`
- `extract(content, scoring_result, prompt)`
- `format_telegram(extraction_result, prompt)`

Typed errors:

- `llm_rate_limit`
- `llm_timeout`
- `quota_exhausted`
- `provider_unavailable`
- `parse_error`

Do not connect live credentials until stub and fixture tests pass.

### `fetchers/base.py`

Define a fetcher interface:

- `fetch(url) -> FetchedContent | TypedError`

Typed errors:

- `auth_invalid`
- `content_blocked`
- `fetch_timeout`
- `fetch_failed`

Use fixture fetchers first. Live Agent-Reach/Web/RSS adapters come after pipeline contract tests pass.

### `pipeline.py`

Pipeline stages:

```text
dequeue or direct URL
fetch
validate content
score with active bundle
extract with active bundle
write Obsidian
push Telegram
mark queue
```

Dry-run mode:

- no Obsidian write
- no Telegram push
- queue may use temporary DB only
- returns complete `ProcessResult`

Staging mode:

- writes to a staging Obsidian folder
- Telegram uses explicit test chat or stub
- scheduled tasks use a tiny fixture source list

Live mode:

- only after staging passes
- writes to configured Obsidian folder
- pushes Telegram
- starts scheduler/daemon

## Scheduler And Live Gate

Do not start live daemon just because the scheduler exists.

Required gates before live:

1. `pytest` passes.
2. `compileall` passes.
3. Runtime Guard validates V3 paths.
4. Dry-run single URL succeeds.
5. Fixture batch succeeds.
6. Staging Obsidian write succeeds.
7. Telegram stub succeeds.
8. Operator explicitly enables live Telegram and scheduler config.

Nightly scheduled sources should be imported as V3 config, not by reading V2 queue.

## Obsidian Output Contract

Future Obsidian frontmatter should include:

- `title`
- `url`
- `final_score`
- `score`
- `signal_tier`
- `source_type`
- `source_tier`
- `decision_window_status`
- `interest_flag`
- `prompt_bundle`
- `runtime_fingerprint`
- `processed_at`

Output path must be recorded before queue can be marked `done`.

## Telegram Output Contract

Telegram should use plain text and plain URLs. No Markdown parse mode by default.

Success brief includes:

- title
- score
- window
- source type/tier
- interest flag
- signal
- evidence
- action
- attribution
- link

Failure notice includes:

- stage
- failure kind
- retry count
- next retry time or next action
- short reason

## Phase 2 Acceptance

Phase 2 is complete when:

- prompt registry can switch and parallel-test bundles
- stub fetcher + stub LLM + parser + pipeline can process fixtures
- queue statuses are correct for success, reject, retry, terminal failure, parse error, fetch failure
- dry-run uses only temporary state
- no live daemon is started
- no V2 queue or V2 secrets are read

Live scheduler/Obsidian/Telegram starts only in the following phase after staging verification.
