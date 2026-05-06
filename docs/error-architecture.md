# V3 Error Architecture

This document maps V2 historical errors O1-O9 to V3 structural defenses and Phase 1 test entry points.

## O1. Daemon Stopped Silently

V2 problem: scheduler or child process could exit without alerting, causing Telegram submissions to be missed.

V3 defense:

- Future daemon roles must write heartbeat and runtime fingerprint.
- Process exit must become a structured `process_exit` event.
- Supervisor must restart only the failed role.
- Start, exit, and restart events must notify admins once live Telegram exists.

Phase 1 test entry:

- `tests/test_runtime_guard.py` validates runtime fingerprint generation and isolation before live roles exist.

## O2. Queue Consumption Failed Silently

V2 problem: fetch failure could still mark a task as done.

V3 defense:

- Queue statuses distinguish `pending`, `processing`, `retry_scheduled`, `done`, `rejected`, and `failed_terminal`.
- `done` is only valid after the output loop closes.
- Failure rows carry `failure_kind`, `last_error`, and `next_action`.

Phase 1 test entry:

- `tests/test_queue_store_contract.py` checks distinct done, rejected, retry, and terminal failure states.

## O3. Twitter Cookie Expired Without Refresh

V2 problem: auth failures looked like short content and were not typed as authentication errors.

V3 defense:

- Fetchers must return typed errors such as `auth_invalid`, `content_blocked`, and `fetch_timeout`.
- Auth failures must route to `manual_review` or `auth_refresh_required`.
- X/Twitter health checks must be independent from content tasks in future phases.

Phase 1 test entry:

- Queue contract includes `FailureKind.AUTH_INVALID` and `NextAction.AUTH_REFRESH_REQUIRED`.

## O4. RSS Time Parser Missed ISO 8601

V2 problem: some valid RSS timestamps were skipped because only RFC822 parsing was supported.

V3 defense:

- Future RSS parser must support RFC822 and ISO 8601.
- Parser warnings must be source-level events.
- Bad timestamps must not silently pass as fresh.

Phase 1 test entry:

- Deferred until RSS parser exists; this document keeps the invariant visible.

## O5. Generic Web Fetch Timeout Was Opaque

V2 problem: some web fetches could hang or fail without clear proxy/fallback trace.

V3 defense:

- Fetchers must have per-channel timeout budgets.
- Proxy and fallback attempts must be recorded.
- Final failure must include last error and attempted path.

Phase 1 test entry:

- Queue contract includes `FailureKind.FETCH_TIMEOUT` and `FailureKind.FETCH_FAILED`.

## O6. RSS Time Filter Let Old Items Through

V2 problem: missing or invalid publication time could default to recent and cause history backfill.

V3 defense:

- Missing or invalid time defaults to skip plus warning.
- First RSS run must enforce a maximum history window, such as 7-30 days.
- Full backfill requires explicit operator intent.

Phase 1 test entry:

- Deferred until RSS parser exists; future tests must cover missing, bad, RFC822, and ISO 8601 dates.

## O7. LLM Rate Limits Caused Batch Failure

V2 problem: scheduler and queue worker could hit the same provider quota and continue failing tasks.

V3 defense:

- Provider-level circuit breaker must be shared by future consumers.
- Rate limit and quota errors become `llm_rate_limit`.
- Affected tasks use `retry_scheduled` with `next_retry_at`.
- Manual Telegram submissions get higher priority than batch RSS.

Phase 1 test entry:

- Queue contract includes `FailureKind.LLM_RATE_LIMIT`, `NextAction.RETRY_LATER`, and `retry_scheduled`.

## O8. Runtime Version Drift And Shadow State

V2 problem: long-running processes used old code and shadow HOME paths, creating alternate queues.

V3 defense:

- Runtime Guard rejects V2 project/state/config paths.
- Runtime Guard rejects known shadow HOME markers.
- Runtime Guard rejects existing queue DBs missing required V3 columns.
- Future live roles must emit and compare runtime fingerprints.

Phase 1 test entry:

- `tests/test_runtime_guard.py` covers V2 queue path, V2 project root, shadow HOME, missing schema, and clean V3 paths.

## O9. LLM Extraction Timeout Broke Single-Link Closure

V2 problem: long extraction timeout became a generic failure without retry context.

V3 defense:

- Timeouts are typed as `llm_timeout`.
- Retryable timeouts use `retry_scheduled`.
- Exhausted retries may produce degraded briefs in future phases.
- User-facing failure notices must include retry count and next action once Telegram output exists.

Phase 1 test entry:

- Queue contract includes `FailureKind.LLM_TIMEOUT` and retry scheduling semantics.

## Global Rules

- Never mark fetch, LLM, parse, or output failure as `done`.
- Never use V2 queue DB, V2 state root, V2 config secrets, V2 logs, or V2 processes.
- Every terminal or retryable failure must be typed.
- Prompt parsing failures must stop before Obsidian or Telegram output.
