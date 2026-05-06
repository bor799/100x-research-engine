# Phase 3 Shadow Run Real URL Test Plan

Created: 2026-04-28

This document is for the next independent Codex session. It defines a controlled
shadow run using the real V3 repo, the real V3 prompt registry, an isolated V3
queue, and a staging Obsidian output directory.

Assumption: the user's phrase about the old "信息源" is interpreted as a test
mix centered on The Information-style startup/AI intelligence sources plus public
startup and VC media. This plan does not read V2 queue, V2 source config, V2
cookies, V2 secrets, or V2 state.

## Goal

Validate whether V3's primary-market prompt bundle produces useful investment
briefs from real 2026 startup/AI articles before any live scheduler, live daemon,
real Telegram bot, or real Obsidian vault write is enabled.

The run should answer:

- Does `primary_market_v1` correctly accept high-signal funding/product articles?
- Does it reject broad, promotional, or low-actionability pieces?
- Does frontmatter include enough metadata for Obsidian review?
- Are filenames stable and safe?
- Are fetch failures, paywalls, parse errors, and output failures visible and not
  marked `done`?
- Can the same URL be re-run without corrupting queue state?

## Hard Safety Rules

- Do not start live daemon.
- Do not start live scheduler.
- Do not start live Telegram bot.
- Do not call Telegram Bot API.
- Do not write the real Obsidian vault.
- Do not read `~/.100x_v2/queue.db`.
- Do not read V2 source config, tokens, cookies, `.env`, logs, or state.
- Do not write real `~/.100x_v3/queue.db` during the first shadow run.
- Use only an explicit temporary or shadow state root.

Recommended shell envelope:

```bash
export PROJECT_ROOT="/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3"
export SHADOW_ROOT="/tmp/100x-v3-shadow-$(date +%Y%m%d-%H%M%S)"
export STATE_ROOT="$SHADOW_ROOT/.100x_v3"
export QUEUE_DB_PATH="$STATE_ROOT/queue.db"
export STAGING_OBSIDIAN_ROOT="$SHADOW_ROOT/obsidian-staging"
export PYTHONPATH="$PROJECT_ROOT/src"
```

Expected storage:

```text
$STATE_ROOT/queue.db
$STATE_ROOT/runtime_fingerprint.json
$STAGING_OBSIDIAN_ROOT/obsidian/*.md
$STAGING_OBSIDIAN_ROOT/telegram_stub.log
```

## Current Code Gap

As of Phase 2, the backend core is ready for fixture-based dry-run/staging, but
real URL ingestion is not implemented yet. The next session should first add a
small shadow-only adapter and runner:

1. `WebPageFetcher` or equivalent adapter returning V3 `FetchedContent`.
2. A URL-list runner that reads `config/shadow-run-url-candidates.example.yaml`.
3. Runtime Guard before queue initialization.
4. Explicit `STATE_ROOT`, `QUEUE_DB_PATH`, and staging output path.
5. No live scheduler. If a scheduler shim is added, it must be shadow-only and
   limited to this candidate list.

Do not make the first real-URL run depend on Telegram, X, WeChat, or logged-in
browser state. The Information links should be treated as paywall/content-block
tests unless an explicit test credential path is added in a later phase.

## Test List Summary

The machine-readable list is in:

```text
config/shadow-run-url-candidates.example.yaml
```

Use all 23 candidates only after a 3-item smoke passes.

### Smoke Set

| ID | Expected | Why |
|---|---|---|
| `tc-scaleops-2026-03-30` | `done` or useful high score | Concrete AI infrastructure funding, customer and growth details. |
| `tc-google-cloud-next-startups-2026-04-22` | likely `rejected` or medium score | Multi-company conference-style piece; tests broad source filtering. |
| `ti-openclaw-2026-04-22` | `failed_terminal/content_blocked` or auth-style failure unless fetcher can access it | Paywall/auth boundary test. |

### Full Batch Intent

| Group | Count | Expected behavior |
|---|---:|---|
| Startup funding/product signal | 13 | Mostly `done`, useful briefs. |
| Market/sector context | 4 | Some `done`, some medium score; useful for trend synthesis. |
| Broad/promotional/control | 3 | Mostly `rejected` or low score. |
| Paywall/content-block edge | 3 | Not `done` unless content is genuinely fetched. |

## Candidate Notes

### High-Signal Startup/Funding Articles

These should generally produce `done` in staging if the fetcher can extract the
article body:

- TechCrunch, 2026-03-30: ScaleOps raises $130M to improve computing efficiency amid AI demand.
- TechCrunch, 2026-02-05: Fundamental raises $255M Series A with a new take on big data analysis.
- TechCrunch, 2026-03-05: Lio raises $30M from Andreessen Horowitz and others to automate enterprise procurement.
- TechCrunch, 2026-03-30: Rebellions raises $400M at $2.3B valuation in pre-IPO round.
- TechCrunch, 2026-03-11: Rivian spin-out Mind Robotics raises $500M for industrial AI-powered robots.
- TechCrunch, 2026-01-07: Intel spinout Articul8 raises more than half of $70M round at $500M valuation.
- TechCrunch, 2026-02-10: Runway raises $315M at $5.3B valuation.
- TechCrunch, 2026-01-28: Outtake raises $40M for agentic cybersecurity.
- TechCrunch, 2026-01-14: depthfirst raises $40M Series A.
- TechCrunch, 2026-01-14: Skild AI hits $14B valuation.
- VentureBeat, 2026-04-15: Traza raises $2.1M for AI procurement workflows.
- Axios, 2026-04-15: Hilbert raises $28M led by a16z.
- Sifted, 2026-02-25: Wayve raises $1.2B backed by Nvidia, Microsoft, SoftBank, and Uber.

### Sector/Market Context

These are useful to test whether the prompt can extract market structure without
overstating a single-company investment signal:

- Crunchbase News, 2026-04-01: Q1 2026 shatters venture funding records as AI pushes startup investment to $300B.
- Crunchbase News, 2026-04-02: Foundational AI startup funding in Q1 was double all of 2025.
- Crunchbase News, 2026-04-17: Autonomous vehicle funding more than triples in 2026.
- TechCrunch, 2026-02-17: 17 US-based AI companies raised $100M or more in 2026.

### Low-Signal / Rejection Controls

These should not be blindly promoted to strong investment briefs:

- TechCrunch, 2026-04-22: Google Cloud Next 2026 startup showcase.
- TechCrunch, 2026-04-25: Why Tokyo is the most important tech destination of 2026.
- Sifted, 2026-04-09: AI is rewriting the rules of European entrepreneurship.

### Paywall / Content-Blocked Edge Cases

These are intentional boundary tests. The correct result is not necessarily a
brief; it may be a typed fetch/content-blocked failure. The key invariant is:
never mark these `done` without real article text.

- The Information, 2026-04-22: OpenClaw Struggles to Grow Up After Overnight Success.
- The Information, 2026-02-14: Silicon Valley CEOs Find New Ethos From AI Tools: I'll Do It Myself.
- The Information, 2026-02-07: After OpenClaw, a Wild, Weird Age of Consumer Agents Lies Ahead.

## Execution Plan

### Step 0: Preflight

Run:

```bash
cd "$PROJECT_ROOT"
python -m pytest
python -m compileall src tests
```

Confirm:

- all tests pass;
- `QUEUE_DB_PATH` points under `$STATE_ROOT`;
- no default `~/.100x_v3/queue.db` is created or modified;
- Runtime Guard accepts the shadow paths.

### Step 1: Implement Shadow-Only Real URL Fetcher

Minimal acceptable behavior:

- `fetch(url) -> FetchedContent | TypedError`.
- Sets `source`, `source_type`, `title`, `text`, `raw`, `fetched_at`, `content_hash`,
  and `metadata`.
- Uses network only for the provided URL.
- Handles HTTP errors, paywalls, empty content, and parse failures as typed errors.
- Does not use logged-in browser state by default.
- Does not read V2 cookies or V2 credentials.

Recommended failure mapping:

| Condition | FailureKind | NextAction |
|---|---|---|
| HTTP 401/403 or visible paywall | `content_blocked` or `auth_invalid` | `manual_review` |
| timeout | `fetch_timeout` | `retry_later` |
| empty extracted body | `validation_failed` | `drop` or `manual_review` |
| parser/readability failure | `fetch_failed` | `manual_review` |

### Step 2: 3-Item Smoke

Run only:

```text
tc-scaleops-2026-03-30
tc-google-cloud-next-startups-2026-04-22
ti-openclaw-2026-04-22
```

Pass conditions:

- ScaleOps produces a staging markdown file and queue `done`.
- Google Cloud Next either produces a medium/low brief or is rejected; it should
  not look like a high-conviction single-company investment signal.
- The Information item either gets a real body or fails explicitly with typed
  blocked/auth error; it must not be `done` with an empty or teaser-only body.

### Step 3: 10-Item Batch

Run the first 10 non-paywalled candidates. Inspect:

- title extraction quality;
- frontmatter correctness;
- filename safety;
- `final_score` distribution;
- `signal_tier` distribution;
- low-quality reject behavior;
- duplicate URL behavior by re-running one already processed URL.

Suggested acceptance:

- No `done` row has empty `output_path`.
- No output file is outside `$STAGING_OBSIDIAN_ROOT`.
- No more than 1 parse failure across public articles.
- `rejected` remains distinct from `failed_terminal`.

### Step 4: Full Candidate Batch

Run all 23 candidates with `run_parallel_tests=True` if available.

Review:

- active bundle result quality;
- `v2_legacy` parallel comparison metadata;
- whether high-signal articles are too uniformly high;
- whether broad market pieces are scored differently from concrete startup
  announcements;
- whether content-blocked items remain visible and non-`done`.

### Step 5: Human Review Rubric

For each generated markdown:

| Dimension | Good signal |
|---|---|
| Title | Specific company/source, no generic scrape noise. |
| One-line signal | Says what changed and why it matters. |
| Evidence | Uses article facts, not generic AI hype. |
| Inferences | Clearly separates facts from interpretation. |
| Actions | Gives monitorable next steps. |
| Score | Matches investor actionability, not article excitement. |
| Frontmatter | Includes URL, score, final_score, signal_tier, prompt bundle, processed_at. |

### Step 6: Stop Criteria

Stop and fix before continuing if:

- any live Telegram call occurs;
- any file is written to the real Obsidian vault;
- any queue appears under `~/.100x_v3/queue.db` unintentionally;
- any V2 path appears in logs or fingerprints;
- paywalled teaser text is marked `done`;
- fetch/output failure is marked `done`;
- more than 20% of public articles become parse failures.

## Review Output

At the end of the next run, produce:

```text
$SHADOW_ROOT/report.md
```

Required sections:

- Run timestamp and environment paths.
- Candidate count and status counts.
- Output file list.
- Failed/rejected URL table.
- Three best briefs.
- Three worst briefs.
- Prompt adjustment notes.
- Whether to proceed to shadow schedule.

## Next Codex Prompt

```text
请按 ai-reading/phase3-shadow-run-real-url-test-plan.md 执行 V3 shadow run。
先不要启动 live daemon/scheduler/Telegram bot。
不要读取 V2 queue/config/secrets。
实现 shadow-only real URL fetcher 和 URL-list runner，读取 config/shadow-run-url-candidates.example.yaml。
使用临时 STATE_ROOT/QUEUE_DB_PATH 和 staging Obsidian 输出目录。
先跑 3-item smoke，通过后再跑 10-item batch；最后生成 $SHADOW_ROOT/report.md。
```
