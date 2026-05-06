# Phase 4 Live Backend Architecture And Production Rollout Plan

Created: 2026-04-28

本文件用于下一次独立开发 session。它承接 Phase 2 的 dry-run/staging
后端核心，以及用户已经完成并满意的 Phase 3 real URL shadow run。

目标是进入下一阶段：把 V3 从 shadow-run 工具升级为一个可以稳定运行的
正式后端，支持定时抓取多个信息源、Telegram 手动提交、真实 LLM、正式
Obsidian 写入、Telegram 推送、状态闭环、运行监控和可回滚发布。

这里的“正式库”定义为：

```text
真实 V3 runtime state
  + 真实 V3 queue.db
  + 真实 source config
  + 真实 scheduler/worker/bot 角色
  + 真实 Obsidian vault 输出目录
  + 真实 Telegram delivery
  + 可审计日志、fingerprint、health check、rollback gate
```

它仍然不是 V2 的延续。Phase 4 不读取 V2 queue，不复用 V2 state，不复制
V2 secrets。若需要复用旧信息源，只能人工整理成新的 V3 source config。

## Current Context

### 已完成

- V3 repo 与 V2 分离。
- `RuntimeGuard` 能拒绝 V2 path、shadow HOME、queue path 越界和 schema
  不兼容。
- `QueueStore` 已有固定状态机：

```text
pending
processing
retry_scheduled
done
rejected
failed_terminal
```

- `PromptRegistry` 已支持 active bundle 与 parallel test bundle。
- Phase 2 pipeline 已支持：
  - fixture fetcher;
  - stub LLM;
  - prompt parser;
  - dry-run output;
  - staging Obsidian output;
  - Telegram stub;
  - active bundle output;
  - parallel bundle evaluation.
- Phase 3 已新增 shadow-only real URL 能力：
  - `fetchers/web.py`;
  - `llm/shadow.py`;
  - `shadow_runner.py`;
  - `config/shadow-run-url-candidates.example.yaml`;
  - 真实 URL shadow run 结果已由用户确认“比较满意”。

### 当前关键缺口

- `Pipeline` 仍拒绝 `RuntimeMode.LIVE`。
- `OutputPort` 只有 dry-run/staging，没有 live Obsidian + live Telegram port。
- `LLMProvider` 还没有真实 provider route。
- 没有 source registry 和 scheduled ingestion。
- 没有 queue worker loop。
- 没有 Telegram inbound bot。
- 没有 operator command wrapper，例如 `scripts/run-v3.sh`。
- 没有 live health check、stale processing recovery、lock/fingerprint 文件。
- 没有生产级重试、去重、限速和 shutdown 语义。

## Phase 4 Design Principles

### 1. Live 是显式授权，不是默认行为

任何 live 角色启动前必须同时满足：

- `RuntimeGuard.validate(write_fingerprint=True)` 通过。
- `config/config.local.yaml` 存在。
- `live.enabled: true`。
- `outputs.obsidian_root` 是明确的本地 Obsidian 目录。
- Telegram token/chat id 只从 env 或 untracked local config 读取。
- `QUEUE_DB_PATH` 在 `STATE_ROOT` 下。
- `project_root` 是 V3 repo。

### 2. Ingress 只入队，不直接跑 pipeline

所有输入来源统一变成 `QueueTask`：

```text
manual URL command
RSS scheduled item
web source scheduled item
X/Twitter adapter item
WeChat adapter item
Telegram inbound URL
```

Ingress 不调用 LLM，不写 Obsidian，不推 Telegram。它只负责：

- source config 读取;
- URL 或 content candidate 发现;
- 去重;
- priority 设定;
- reply channel/chat id 设定;
- enqueue。

### 3. Fetcher 是适配层，不是状态中心

Fetcher 只做：

```text
url/source item -> FetchedContent | TypedError
```

Fetcher 不读取 queue，不更新 queue，不读取 V2 credential，不写 Obsidian。

### 4. Queue worker 是唯一处理者

Queue worker 按顺序或小并发处理 `pending/retry_scheduled`：

```text
dequeue -> mark processing -> pipeline -> queue final state
```

第一版 live worker 建议单 worker、单并发。稳定后再加小并发。

### 5. Output loop closed 才能 done

正式 `done` 必须同时满足：

- Obsidian markdown 已写入正式 vault 或 live output 目录;
- Telegram 成功推送，或 config 明确关闭 Telegram push;
- `QueueStore.mark_done()` 记录真实 `output_path`;
- output manifest 记录 task id、content hash、prompt hash、runtime fingerprint。

任何 fetch、LLM、parse、format、Obsidian write、Telegram push 失败都不能
变成 `done`。

### 6. Active output 与 evaluation 继续分离

- `active_bundle` 写正式 Obsidian 和 Telegram。
- `parallel_test_bundles` 只写 evaluation metadata 或 JSONL。
- parallel 失败不影响 active task 最终状态。

## Target Architecture

```text
RuntimeGuard
  -> ConfigLoader
  -> SourceRegistry
  -> Ingress
       - manual URL
       - scheduler
       - Telegram inbound
       - future X/WeChat adapters
  -> QueueStore
  -> QueueWorker
       - Web/RSS/X/WeChat Fetcher
       - Real LLM Provider
       - PromptRegistry
       - PromptParser
       - Pipeline
       - LiveOutputPort
            - LiveObsidianWriter
            - TelegramClient
  -> HealthCheck
  -> Operator commands
```

## Runtime Modes

Keep the three modes:

```python
class RuntimeMode(str, Enum):
    DRY_RUN = "dry_run"
    STAGING = "staging"
    LIVE = "live"
```

Phase 4 change:

- `DRY_RUN`: unchanged, no file write, no Telegram network.
- `STAGING`: unchanged for test output; can use real URL fetch and real LLM if configured.
- `LIVE`: allowed only when `LiveGate` passes.

Add:

```python
class LiveRole(str, Enum):
    WORKER = "worker"
    SCHEDULER = "scheduler"
    TELEGRAM_BOT = "telegram_bot"
    HEALTHCHECK = "healthcheck"
    COMMAND = "command"
```

Each live role writes its own lock/fingerprint file under:

```text
$STATE_ROOT/roles/{role}.json
$STATE_ROOT/locks/{role}.lock
$STATE_ROOT/logs/{role}.log
```

## Production Configuration

Tracked config remains example-only. Real config lives in untracked:

```text
config/config.local.yaml
```

Recommended schema:

```yaml
runtime:
  mode: live
  state_root: "~/.100x_v3"
  queue_db_path: "~/.100x_v3/queue.db"
  log_dir: "~/.100x_v3/logs"
  lock_dir: "~/.100x_v3/locks"
  role_dir: "~/.100x_v3/roles"

live:
  enabled: false
  require_runtime_guard: true
  require_operator_confirmation: true
  max_tasks_per_run: 20
  max_consecutive_failures: 5

llm:
  provider: "zhipu"          # example only
  api_key_env: "ZHIPU_API_KEY"
  scoring_model: ""
  extraction_model: ""
  telegram_brief_model: ""
  request_timeout_seconds: 90
  max_retries: 2
  min_delay_seconds: 2

prompts:
  registry: "prompts/registry.json"
  active_bundle: "primary_market_v1"
  parallel_test_bundles:
    - "v2_legacy"

outputs:
  obsidian_root: ""          # real local vault subdirectory
  obsidian_subdir: "100X/Inbox"
  write_manifest: true
  telegram_enabled: true
  telegram_bot_token_env: "TELEGRAM_BOT_TOKEN"
  telegram_admin_chat_id_env: "TELEGRAM_ADMIN_CHAT_ID"
  telegram_default_chat_id_env: "TELEGRAM_DEFAULT_CHAT_ID"

scheduler:
  enabled: false
  interval_minutes: 60
  max_new_items_per_source: 5
  max_total_new_items_per_tick: 20
  lookback_days: 7
  jitter_seconds: 30

worker:
  enabled: true
  batch_size: 5
  poll_interval_seconds: 15
  retry_scan_limit: 20
  processing_stale_after_minutes: 30

sources:
  - id: techcrunch_ai
    enabled: true
    type: rss
    priority: 80
    url: "https://techcrunch.com/category/artificial-intelligence/feed/"
    tags: ["ai", "startup", "public_web"]

  - id: crunchbase_news
    enabled: true
    type: rss
    priority: 90
    url: "https://news.crunchbase.com/feed/"
    tags: ["venture", "funding"]

  - id: manual_watchlist
    enabled: true
    type: url_list
    priority: 50
    path: "config/manual-watchlist.local.yaml"

telegram_bot:
  enabled: false
  allowed_chat_ids_env: "TELEGRAM_ALLOWED_CHAT_IDS"
  manual_url_priority: 10
  reject_unknown_chat: true
```

Important: `live.enabled` and `scheduler.enabled` default to `false`.

## Source Registry

Add `src/knowledge_extractor_v3/sources/`.

Suggested modules:

```text
sources/models.py
sources/registry.py
sources/rss.py
sources/url_list.py
sources/telegram_ingress.py
sources/dedupe.py
```

### SourceItem

```python
@dataclass(frozen=True)
class SourceItem:
    source_id: str
    source_type: str
    url: str
    title: str = ""
    published_at: str = ""
    author: str = ""
    priority: int = 100
    reply_channel: str = ""
    reply_chat_id: str = ""
    metadata: dict[str, object] = field(default_factory=dict)
```

### Source Adapter Contract

```python
class SourceAdapter(Protocol):
    def discover(self, source_config: SourceConfig) -> list[SourceItem] | TypedError:
        ...
```

RSS adapter first. X and WeChat later.

### RSS Requirements

- Support RFC822 and ISO 8601 dates.
- Enforce `lookback_days`.
- Skip old items.
- Deduplicate by normalized URL.
- Return typed source-level warnings for bad timestamps.
- Do not process article bodies inside RSS adapter.

## Queue Store Extensions

Existing schema is enough for first live run, but Phase 4 should add safe helper
methods without breaking old tests:

```python
QueueStore.next_ready_tasks(limit: int, now: str) -> list[QueueTask]
QueueStore.recover_stale_processing(before: str) -> int
QueueStore.count_by_status() -> dict[str, int]
QueueStore.find_by_url(url: str) -> QueueTask | None
```

Do not change status values.

Future optional columns:

```text
source_id
dedupe_key
content_hash
scheduled_at
last_attempt_at
```

If columns are added, update `QUEUE_REQUIRED_COLUMNS` and migration tests.

## Fetcher Strategy

Phase 4 first live fetchers:

1. `WebPageFetcher` hardening.
2. `RSSSourceAdapter` for discovery only.
3. Optional `UrlListSourceAdapter`.

Do not implement X/WeChat live auth in the same step as scheduler + live output.
Those should be separate adapters once the production loop is stable.

### WebPageFetcher Hardening

Add:

- canonical URL metadata;
- redirect URL metadata;
- content language if detectable;
- min word count from config;
- max bytes from config;
- paywall marker config;
- source-specific extraction fallback if needed.

Typed failure mapping:

| Condition | FailureKind | NextAction |
|---|---|---|
| HTTP 401/403 | `auth_invalid` or `content_blocked` | `manual_review` |
| visible paywall/teaser | `content_blocked` | `manual_review` |
| timeout | `fetch_timeout` | `retry_later` |
| network transient | `fetch_failed` retryable if clearly transient | `retry_later` |
| unsupported content | `validation_failed` | `drop` |
| empty/too short body | `validation_failed` | `manual_review` |

## Real LLM Provider

Add `src/knowledge_extractor_v3/llm/live_provider.py`.

The provider should implement the existing `LLMProvider` protocol:

```python
score(content, prompt) -> str | TypedError
extract(content, score, prompt) -> str | TypedError
format_telegram(score, extraction, prompt) -> str | TypedError
```

Rules:

- Read API key from env only.
- Never log raw secrets.
- Use per-stage timeouts.
- Map provider rate limits to `LLM_RATE_LIMIT`.
- Map request timeout to `LLM_TIMEOUT`.
- Preserve raw LLM text in `ScoreResult.raw_text` / `ExtractionResult.raw_text`.
- Parser remains the only place that validates JSON schema.
- No provider-specific objects leak into pipeline.

Recommended first implementation:

- one synchronous provider;
- no streaming;
- one retry inside provider for transient HTTP 5xx;
- no auto retry for parse errors.

## Live Output Ports

Add:

```text
outputs/live_obsidian.py
outputs/live_telegram.py
outputs/live.py
```

### LiveObsidianWriter

Inputs:

- real vault root;
- subdirectory;
- `FetchedContent`;
- `ScoreResult`;
- `ExtractionResult`;
- runtime fingerprint;
- prompt hash;
- task id.

Requirements:

- Ensure destination path stays under configured Obsidian root.
- Write atomically: temp file then rename.
- Filename: date + safe title + content hash.
- Frontmatter includes:
  - title
  - url
  - source
  - source_type
  - source_tier
  - final_score
  - score
  - signal_tier
  - decision_window_status
  - interest_flag
  - content_hash
  - prompt_bundle
  - prompt_hash
  - model_route
  - queue_task_id
  - runtime_fingerprint_hash
  - processed_at
- Write optional sidecar manifest:

```text
{same_filename}.manifest.json
```

### LiveTelegramClient

Requirements:

- Plain text only by default.
- No Markdown/HTML parse mode by default.
- Configurable max length.
- Admin failure notices for terminal failures.
- Manual Telegram submissions reply to original chat when `reply_chat_id` exists.
- Scheduler items go to default chat if enabled.
- Telegram failure must return `OUTPUT_FAILED` and must not mark task `done`
  unless Telegram is disabled by config.

### LiveOutputPort

Semantics:

```text
write Obsidian
  -> if fail: output_failed
send Telegram if enabled
  -> if fail: output_failed
return OutputResult(ok=True, obsidian_path=real_path, telegram_status="sent")
```

If Telegram is disabled:

```text
telegram_status="disabled"
```

and Obsidian success can close the loop.

## Pipeline Change

`Pipeline` should accept an explicit output port mapping or live output port:

```python
Pipeline(
    queue_store,
    fetcher=...,
    llm_provider=...,
    prompt_registry=...,
    output_ports={
        RuntimeMode.DRY_RUN: DryRunOutputPort(),
        RuntimeMode.STAGING: StagingOutputPort(...),
        RuntimeMode.LIVE: LiveOutputPort(...),
    },
)
```

Remove the hardcoded Phase 2 live refusal from `Pipeline`; move the refusal into
`LiveGate` before pipeline construction.

Keep this invariant:

```text
Pipeline never starts scheduler or bot.
Pipeline only processes one URL/task.
```

## Queue Worker

Add `src/knowledge_extractor_v3/worker.py`.

Responsibilities:

- Runtime Guard on start.
- Recover stale `processing` tasks.
- Read ready tasks by priority and retry time.
- Process each task through pipeline.
- Respect `batch_size`.
- Stop on max consecutive failures.
- Log one JSON line per task result.
- Exit cleanly on SIGINT/SIGTERM.

First live worker should support:

```bash
python -m knowledge_extractor_v3.worker --once --limit 5
python -m knowledge_extractor_v3.worker --loop
```

No multiprocessing in the first version.

## Scheduler

Add `src/knowledge_extractor_v3/scheduler.py`.

Responsibilities:

- Runtime Guard on start.
- Load source registry.
- Discover source items.
- Enqueue new URLs only.
- Apply source priority.
- Enforce per-source and per-tick limits.
- Write scheduler event log.
- Never call LLM or output ports.

Commands:

```bash
python -m knowledge_extractor_v3.scheduler --once --limit 20
python -m knowledge_extractor_v3.scheduler --loop
```

First scheduler source types:

- `rss`;
- `url_list`.

Later source types:

- `telegram_inbox`;
- `x`;
- `wechat`;
- custom skill runner.

## Telegram Bot

Add only after worker + scheduler + live output are stable.

Add `src/knowledge_extractor_v3/telegram_bot.py`.

Responsibilities:

- Runtime Guard on start.
- Read token from env.
- Accept URL messages from allowed chat ids only.
- Enqueue URLs with high priority.
- Set `reply_channel="telegram"` and `reply_chat_id`.
- Reply with queue id and initial status.
- Never run pipeline inside bot.

Bot commands:

```text
/url <url>
/status <queue_id>
/recent
/failed
/help
```

Do not implement rich admin workflows until the basic loop is stable.

## Operator Commands

Add `scripts/run-v3.sh`.

Required commands:

```bash
./scripts/run-v3.sh preflight
./scripts/run-v3.sh status
./scripts/run-v3.sh enqueue-url "https://example.com/article"
./scripts/run-v3.sh worker-once --limit 5
./scripts/run-v3.sh scheduler-once --limit 10
./scripts/run-v3.sh staging-schedule --limit 10
./scripts/run-v3.sh live-worker
./scripts/run-v3.sh live-scheduler
./scripts/run-v3.sh live-bot
./scripts/run-v3.sh stop-role worker
./scripts/run-v3.sh logs worker
```

`live-*` commands must refuse unless:

- `config/config.local.yaml` exists;
- `live.enabled: true`;
- required env vars are present;
- Runtime Guard passes;
- `python -m pytest` and `python -m compileall src tests` were recently run or
  preflight is explicitly invoked in the command.

Avoid broad `pkill`. Stop role-specific processes by lock/pid metadata.

## Health Check And Recovery

Add `src/knowledge_extractor_v3/health.py`.

Checks:

- V3 path isolation.
- Queue schema.
- Queue status counts.
- Stale `processing` tasks.
- Role lock freshness.
- Recent scheduler tick.
- Recent worker task result.
- Telegram token presence if enabled.
- Obsidian root exists and is writable if live output enabled.
- Source config parseable.
- Prompt registry validates.

Recovery:

- stale `processing` -> `retry_scheduled`;
- broken role lock -> mark stale, do not kill arbitrary process;
- missing output path on done should be impossible; if found, report critical.

## Observability

Write JSONL logs under:

```text
$STATE_ROOT/logs/
```

Suggested files:

```text
worker.jsonl
scheduler.jsonl
telegram_bot.jsonl
health.jsonl
task_results.jsonl
source_events.jsonl
output_manifest.jsonl
```

Each task result log should include:

- queue task id;
- URL;
- source;
- final status;
- failure kind;
- next action;
- output path;
- prompt bundle;
- prompt hash;
- model route;
- content hash;
- stage durations;
- runtime fingerprint hash.

## Rollout Plan

### Phase 4A: Production Config And Live Gate

Implement:

- `ConfigLoader`;
- `LiveGate`;
- production config validation;
- preflight command;
- Runtime Guard fingerprint writing.

Acceptance:

- live mode refuses by default;
- live mode refuses missing env vars;
- live mode refuses output path outside configured root;
- tests prove V2 paths still rejected.

### Phase 4B: Live Output Port

Implement:

- `LiveObsidianWriter`;
- `LiveTelegramClient` with network mocked in tests;
- `LiveOutputPort`;
- pipeline output port injection.

Acceptance:

- live output writes only under configured Obsidian root;
- write is atomic;
- Telegram failure prevents `done`;
- Telegram disabled allows Obsidian-only `done`;
- staging behavior unchanged.

### Phase 4C: Real LLM Provider

Implement:

- provider config;
- env key loading;
- request/timeout/rate-limit mapping;
- tests with fake HTTP transport or monkeypatch.

Acceptance:

- rate limit -> `retry_scheduled`;
- timeout -> `retry_scheduled`;
- parse error -> `failed_terminal`;
- raw response stored in typed result;
- no secret appears in logs.

### Phase 4D: Queue Worker

Implement:

- ready task selection;
- stale task recovery;
- worker once;
- worker loop;
- JSONL logs.

Acceptance:

- `worker-once --limit 5` processes tasks in priority order;
- failed fetch is not `done`;
- output failure is not `done`;
- stale processing is recoverable;
- SIGTERM exits cleanly.

### Phase 4E: Source Registry And Scheduler

Implement:

- source config models;
- RSS adapter;
- URL-list adapter;
- scheduler once;
- scheduler loop;
- source event logs.

Acceptance:

- RSS date parser supports RFC822 and ISO 8601;
- lookback window enforced;
- duplicate URLs do not create duplicate active work;
- scheduler never calls pipeline;
- scheduler disabled by default.

### Phase 4F: Telegram Inbound Bot

Implement:

- allowed chat id gate;
- URL extraction;
- enqueue with high priority;
- `/status` and `/recent`;
- role logs.

Acceptance:

- unknown chat rejected;
- valid URL creates queue task;
- bot does not process task inline;
- reply chat id is preserved for worker output.

### Phase 4G: Controlled Live Pilot

Run with:

```text
live.enabled: true
scheduler.enabled: false
telegram_bot.enabled: false
worker.batch_size: 3
outputs.telegram_enabled: false first, then true after inspection
```

Pilot sequence:

1. Preflight.
2. Manually enqueue 3 public URLs.
3. Run `worker-once --limit 3` with live Obsidian output and Telegram disabled.
4. Inspect Obsidian files.
5. Enable Telegram default chat.
6. Manually enqueue 3 URLs.
7. Run `worker-once --limit 3`.
8. Inspect Telegram messages and queue state.
9. Enable `scheduler-once --limit 5`.
10. Only after inspection, consider loop roles.

### Phase 4H: Live Scheduler And Bot

Enable one role at a time:

1. worker loop;
2. scheduler loop;
3. Telegram inbound bot.

Keep first 24 hours conservative:

- max 20 scheduled items/day;
- one worker;
- no X/WeChat auth adapters;
- Telegram output enabled only for accepted briefs;
- admin failure notices enabled.

## Test Plan

New tests:

```text
tests/test_config_loader.py
tests/test_live_gate.py
tests/test_live_obsidian_output.py
tests/test_live_telegram_output.py
tests/test_live_pipeline.py
tests/test_queue_worker.py
tests/test_scheduler_rss.py
tests/test_source_registry.py
tests/test_health_check.py
tests/test_run_v3_script.py
```

Must keep existing tests green:

```bash
python -m pytest
python -m compileall src tests
```

Additional live-gated smoke:

```bash
STATE_ROOT="$(mktemp -d)/.100x_v3" \
QUEUE_DB_PATH="$STATE_ROOT/queue.db" \
OBSIDIAN_ROOT="$(mktemp -d)/obsidian" \
PYTHONPATH=src \
python -m knowledge_extractor_v3.worker --once --limit 1 --mode staging
```

Do not run a real live smoke in CI.

## Production Acceptance Criteria

Phase 4 is complete when:

- `run-v3.sh preflight` passes.
- live mode refuses unless explicitly enabled.
- live Obsidian output writes correct markdown and manifest.
- Telegram output can be enabled and disabled safely.
- real LLM provider maps rate-limit/timeout/parse failures correctly.
- scheduler can enqueue RSS/url_list items without processing them.
- worker can process pending/retry tasks.
- Telegram inbound bot can enqueue manual URL submissions.
- health check reports queue counts and stale role state.
- no V2 queue/config/secrets are read.
- no failure path marks task `done`.
- rollback can stop live roles without broad process killing.

## Rollback Plan

If live output quality or operations fail:

1. Stop scheduler role.
2. Stop Telegram bot role.
3. Let worker finish current task or stop worker cleanly.
4. Set `live.enabled: false`.
5. Keep queue DB for forensic review.
6. Move bad Obsidian files to a quarantine folder if needed.
7. Do not delete queue rows until report is written.
8. Generate incident report:

```text
$STATE_ROOT/reports/live-rollback-{timestamp}.md
```

## Implementation Order For Next Session

Recommended exact order:

1. Add `config_loader.py` and `live_gate.py`.
2. Add tests proving live refuses by default.
3. Add output port injection to `Pipeline` while keeping dry-run/staging tests green.
4. Add `LiveObsidianWriter` and atomic write tests.
5. Add `LiveTelegramClient` with mocked HTTP tests.
6. Add `LiveOutputPort`.
7. Add real LLM provider interface implementation behind config, using fake transport in tests.
8. Add QueueStore helper methods for ready tasks and stale recovery.
9. Add `worker.py` once mode.
10. Add source registry and RSS/url_list adapters.
11. Add `scheduler.py` once mode.
12. Add `scripts/run-v3.sh preflight/status/enqueue-url/worker-once/scheduler-once`.
13. Add health check.
14. Add loop modes only after once modes are verified.
15. Add Telegram inbound bot last.

## What Not To Do In The Next Session

- Do not implement X/WeChat live login before RSS/url_list live loop is stable.
- Do not start loop roles automatically.
- Do not enable `live.enabled` in tracked config.
- Do not commit local secrets.
- Do not copy V2 `.env`, cookies, config, queue DB, logs, or `.venv`.
- Do not mark Telegram failure as `done`.
- Do not allow scheduler to call pipeline directly.
- Do not broaden process kill commands.

## Next Codex Prompt

```text
请继续开发 knowledge-extractor/v3，阅读：
- ai-reading/phase4-live-backend-architecture-and-production-rollout-plan.md
- ai-reading/phase2-design-review-and-core-development-plan.md
- ai-reading/phase3-shadow-run-real-url-test-plan.md

用户已完成 Phase 3 real URL shadow run，并对结果满意。现在进入 Phase 4：
生产后端架构与正式库 rollout。

请按 Phase 4 文档的 Implementation Order 开发：
1. 先实现 config_loader.py 和 live_gate.py；
2. 保持 live 默认拒绝；
3. 不启动 live daemon/scheduler/Telegram bot；
4. 不读取 V2 queue/config/secrets；
5. 不写真实 Obsidian vault，除非测试使用临时 OBSIDIAN_ROOT；
6. 完成后运行 python -m pytest、python -m compileall src tests。

如果需要实现 live output，请先只用临时 Obsidian root 和 mocked Telegram HTTP。
```
