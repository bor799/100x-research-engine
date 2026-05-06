# Phase 2 Design Review And Core Development Plan

本文件是给下一次独立开发 session 使用的后端设计校验与执行计划。它基于 Phase 1 已有材料，补齐用户提出的真实目标：

```text
定时抓取多个信息源的近期文章
  + 接收 Telegram 提交的文章
  + 通过可配置 skill/fetcher 适配 X、微信、网页、RSS 等来源
  + 进入 V3 队列
  + 运行可切换 prompt bundle
  + 写入本地 Obsidian
  + 推送 Telegram
  + 用结构化状态闭环
```

Phase 2 的目标不是启动 live 系统，而是把这个闭环的后端骨架做成可 dry-run、可 staging 验证、可切换、可回滚的稳定核心。

## 结论

当前 Phase 2 计划基本满足核心目的，但需要补充三条设计约束：

1. **输入层和处理层分离**：定时源、Telegram 输入、手动 URL、fixture 都只负责生成标准 `QueueTask` 或 `FetchedContent`，不要直接耦合 LLM pipeline。
2. **skill/fetcher 是适配层，不是状态中心**：X、微信、RSS、网页抓取未来可以由 skill 或外部 agent 实现，但必须返回 V3 标准模型和 typed error，不能读取 V2 queue 或 V2 secrets。
3. **active output 与 parallel evaluation 分离**：`active_bundle` 负责正式输出；`parallel_test_bundles` 只产生对照结果，不写 Obsidian、不推 Telegram、不影响队列最终状态。

用户强调“稳定、可切换、轻量 Agent 后端”，所以 Phase 2 应避免过早引入多进程 daemon、复杂调度器、live Telegram bot、真实微信/X 登录态。先把顺序处理链路、状态机、typed error、prompt 切换和 staging 输出跑稳。

## 目标校验

### 能否支持定时抓取多个信息源？

可以，但 Phase 2 只实现接口和 fixture/staging 路径：

- `fetchers/base.py` 定义统一 fetcher contract。
- `fetchers/fixture.py` 用 V3 fixtures 模拟文章来源。
- 未来 live scheduler 只负责读取 V3 source config，并把新 URL 入队。
- X、微信、RSS、网页都作为 source adapter/fetcher，不进入 pipeline 内部。

Phase 2 不实现夜间 scheduler，也不读取 V2 队列。下一阶段再实现 `run-v3.sh dry-run-schedule`、`staging-schedule`、`start`。

### 能否支持 Telegram 文章输入？

可以，但 Phase 2 只保留队列字段和 stub 输出：

- `QueueTask.reply_channel`、`reply_chat_id` 已存在。
- Phase 2 pipeline 可以处理 `source="telegram"` 的任务。
- Telegram 入站 bot 不启动。
- Telegram 出站只用 stub，记录将要发送的文本和状态。

下一阶段 live bot 才负责接收 Telegram URL 并入队。

### 能否支持 X 和微信？

设计上可以，但不要在 Phase 2 直接接 live 登录态：

- X/Twitter：未来通过专门 fetcher 或 skill runner 取内容；auth 失败返回 `auth_invalid`。
- 微信：未来通过专门 fetcher 或人工/Agent 抓取适配器取正文；被挡返回 `content_blocked` 或 `fetch_failed`。
- 所有适配器都必须输出 `FetchedContent`，并带 `source_type`、`source_name`、`fetched_at`、`content_hash`。

抓取策略可以灵活，但返回契约必须稳定。

### 能否形成本地 + Telegram 闭环？

可以，闭环条件应严格定义：

- 高质量内容：Fetch、Validate、Score、Extract、Output 全成功后才 `done`。
- 低质量内容：Score 成功但 `signal_tier="Reject"` 或 `final_score` 低于阈值时 `rejected`，不写正式输出。
- parse error：停止在 parser，不写 Obsidian，不推 Telegram，队列进入 `failed_terminal` 或明确 parse failure 状态。
- rate limit/timeout：进入 `retry_scheduled`。
- output failed：不能 `done`；进入 retry 或 terminal failure。

`QueueStore.mark_done()` 已经要求 `output_path`，这个约束是正确的，Phase 2 pipeline 必须保留。

## 推荐后端形状

```text
Ingress layer
  - direct URL dry-run
  - fixture batch
  - future scheduler
  - future Telegram bot

QueueStore
  - pending
  - processing
  - retry_scheduled
  - done
  - rejected
  - failed_terminal

Pipeline
  - fetch
  - validate
  - score active bundle
  - optionally score/extract parallel test bundles
  - extract active bundle
  - render Obsidian staging output
  - render Telegram stub output
  - update QueueStore

Output ports
  - DryRunOutputPort: no files, no Telegram
  - StagingOutputPort: writes test directory, Telegram stub only
  - Future LiveOutputPort: real Obsidian + real Telegram
```

这个形状足够轻量：一个顺序 queue worker 就能跑通，不需要先做复杂并发。未来如果信息源变多，只扩展 ingress/fetcher，不改 pipeline contract。

## Runtime Mode

Phase 2 应显式区分三种模式：

```python
class RuntimeMode(str, Enum):
    DRY_RUN = "dry_run"
    STAGING = "staging"
    LIVE = "live"
```

Phase 2 只允许 `DRY_RUN` 和 `STAGING`：

- `DRY_RUN`：不写 Obsidian，不推 Telegram；可以返回 `OutputResult` preview；queue.db 必须是临时路径。
- `STAGING`：写 staging 目录；Telegram 只写 stub log 或内存记录；queue.db 仍建议临时路径。
- `LIVE`：先保留枚举和拒绝逻辑，调用时直接报错或需要 explicit gate。不要启动。

## Data Models

在 `src/knowledge_extractor_v3/models.py` 中定义这些模型。优先用 dataclass + enum，避免引入外部依赖。

### FetchedContent

字段建议：

- `url: str`
- `source: str`
- `source_type: str`
- `title: str`
- `text: str`
- `raw: str = ""`
- `author: str = ""`
- `published_at: str = ""`
- `fetched_at: str`
- `content_hash: str`
- `metadata: dict[str, object]`

### TypedError

字段建议：

- `failure_kind: FailureKind`
- `message: str`
- `stage: str`
- `retryable: bool`
- `next_action: NextAction`
- `detail: str = ""`
- `next_retry_at: str = ""`

### StageResult

字段建议：

- `stage: str`
- `ok: bool`
- `started_at: str`
- `ended_at: str`
- `duration_ms: int`
- `error: TypedError | None`
- `detail: dict[str, object]`

### ScoreResult

字段建议：

- `prompt_bundle: str`
- `prompt_hash: str`
- `model_route: str`
- `raw_text: str`
- `parsed: dict[str, object]`
- required schema fields as properties or direct fields:
  - `score`
  - `final_score`
  - `signal_tier`
  - `decision_window_status`
  - `source_type`
  - `source_tier`
  - `interest_flag`
  - `attribution_chain`

### ExtractionResult

字段建议：

- `prompt_bundle: str`
- `prompt_hash: str`
- `model_route: str`
- `raw_text: str`
- `parsed: dict[str, object]`
- `title: str`
- `one_line_signal: str`
- `obsidian_brief_markdown: str`

### OutputResult

字段建议：

- `ok: bool`
- `mode: RuntimeMode`
- `obsidian_path: str = ""`
- `telegram_status: str = ""`
- `telegram_preview: str = ""`
- `error: TypedError | None = None`

### ProcessResult

字段建议：

- `url: str`
- `source: str`
- `queue_task_id: int | None`
- `current_stage: str`
- `final_status: QueueStatus`
- `retryable: bool`
- `failure_kind: FailureKind`
- `next_action: NextAction`
- `output_path: str`
- `telegram_status: str`
- `prompt_bundle: str`
- `stage_results: list[StageResult]`
- `score_result: ScoreResult | None`
- `extraction_result: ExtractionResult | None`
- `parallel_results: list[ProcessResult] | list[dict]`

注意：`parallel_results` 不应递归复杂化太深。实现上可以用 `PromptRunResult` 小模型，比直接嵌套 `ProcessResult` 更稳。

## Prompt Parser

`prompt_parser.py` 负责把 LLM JSON 转成强类型结果：

- 去掉常见 Markdown fence。
- `json.loads` 失败返回 `TypedError(PARSE_ERROR)`。
- scoring 缺必填字段返回 `parse_error`。
- extraction 缺必填字段返回 `parse_error`。
- `final_score` 必须在 `0-1`。
- `score` 必须在 `0-10`。
- 数字字段必须是 int/float，不接受空字符串。

最低必填字段：

Scoring:

```text
score
final_score
signal_tier
L1
L2
L3
L4
objective_quality
decision_window_status
source_type
source_tier
interest_flag
attribution_chain
rationale
key_claims
watch_items
```

Extraction:

```text
title
one_line_signal
decision_window_status
source_type
source_tier
interest_flag
attribution_chain
why_it_matters
evidence
inferences
risks_and_conflicts
recommended_actions
monitoring_triggers
obsidian_brief_markdown
```

Parser error 是 pipeline 的硬停止点，不能进入 output。

## LLM Provider Stub

`src/knowledge_extractor_v3/llm/provider.py` 定义接口和 stub。

接口：

```python
class LLMProvider(Protocol):
    def score(self, content: FetchedContent, prompt: str) -> str | TypedError: ...
    def extract(self, content: FetchedContent, score: ScoreResult, prompt: str) -> str | TypedError: ...
    def format_telegram(self, score: ScoreResult, extraction: ExtractionResult, prompt: str) -> str | TypedError: ...
```

Stub 场景由 fixture URL 或 metadata 驱动：

- `high_signal`：返回合法高分 JSON。
- `low_quality`：返回合法低分/Reject JSON。
- `parse_error`：返回缺字段或非 JSON。
- `llm_rate_limit`：返回 `TypedError(LLM_RATE_LIMIT, retryable=True)`。
- `llm_timeout`：返回 `TypedError(LLM_TIMEOUT, retryable=True)`。

不要接真实 API key，不读取 `.env`，不复用 V2 credential。

## Fetcher Stub And Fixtures

目录建议：

```text
tests/fixtures/articles/
  high_signal_primary_market.md
  low_quality_marketing.md
  parse_error_candidate.md
  llm_rate_limit_candidate.md
  fetch_failed_candidate.md
  output_failed_candidate.md
```

`fetchers/base.py`：

```python
class Fetcher(Protocol):
    def fetch(self, url: str) -> FetchedContent | TypedError: ...
```

`fetchers/fixture.py`：

- 只读 `tests/fixtures` 或调用方传入的 fixture map。
- 不扫描用户 HOME。
- 不读 V2 queue。
- `fixture://fetch_failed` 返回 `FETCH_FAILED`。

## Output Ports

`outputs/obsidian.py`：

- `StagingObsidianWriter(root: Path)` 写入 staging 测试目录。
- 文件名由日期 + safe title + content hash 组成。
- frontmatter 包含 prompt bundle、score、source、processed_at、url。

`outputs/telegram.py`：

- `TelegramStub` 不联网。
- 保存 `telegram_preview` 或写入 staging log。
- 支持模拟 `output_failed`。

Phase 2 不实现 live Obsidian path，也不实现 Telegram Bot API。

## Pipeline Contract

`pipeline.py` 的主入口建议：

```python
class Pipeline:
    def process_url(
        self,
        url: str,
        *,
        source: str = "manual",
        queue_task_id: int | None = None,
        mode: RuntimeMode = RuntimeMode.DRY_RUN,
        prompt_bundle: str | None = None,
        run_parallel_tests: bool = False,
    ) -> ProcessResult: ...
```

处理顺序：

1. Runtime mode gate。
2. enqueue 或读取传入 task。
3. mark `processing`。
4. fetch。
5. validate content，不合格 `rejected`。
6. resolve active prompt bundle。
7. LLM score。
8. parse scoring JSON。
9. 如果低质量/Reject，mark `rejected`。
10. LLM extract。
11. parse extraction JSON。
12. format Telegram stub text。
13. output port 写 staging 或 dry-run preview。
14. mark `done`，必须带 `output_path` 或 dry-run synthetic output id。
15. 任何 failure 根据 typed error 更新 queue。

### Dry-run `done` 的处理

当前 `QueueStore.mark_done()` 要求 `output_path`。Dry-run 又不能真实写文件。因此 Phase 2 可以使用稳定的 synthetic path：

```text
dry-run://{task_id}/{content_hash}
```

这既满足“output loop closed”的语义，也避免写真实文件。

### Parallel Bundle 处理

如果 `run_parallel_tests=True`：

- active bundle 走完整 score/extract/output。
- parallel bundles 只走 score/extract/parser。
- parallel parse error 记录在 `parallel_results`，不影响 active queue status。
- 如果 active bundle 也在 parallel list 中，不重复执行。

## Queue Status Mapping

| 场景 | QueueStatus | FailureKind | NextAction |
|---|---|---|---|
| high_signal 成功 | done | none | none |
| low_quality / Reject | rejected | validation_failed | drop |
| fetch_failed | failed_terminal 或 retry_scheduled | fetch_failed | manual_review 或 retry_later |
| parse_error | failed_terminal | parse_error | investigate |
| llm_rate_limit | retry_scheduled | llm_rate_limit | retry_later |
| llm_timeout | retry_scheduled | llm_timeout | retry_later |
| output_failed | failed_terminal 或 retry_scheduled | output_failed | manual_review 或 retry_later |

第一版建议：

- rate limit / timeout：retry。
- fetch_failed：terminal，除非 fixture 明确 retryable。
- output_failed：terminal，防止误报 done。
- parse_error：terminal + investigate。

## Tests To Implement

新增测试建议：

```text
tests/test_prompt_parser.py
tests/test_llm_provider_stub.py
tests/test_fixture_fetcher.py
tests/test_pipeline_dry_run.py
tests/test_pipeline_staging_outputs.py
```

必测场景：

1. `high_signal`：最终 `done`，有 score/extraction/output result。
2. `low_quality`：最终 `rejected`，不写 Obsidian，不推 Telegram。
3. `parse_error`：最终 `failed_terminal`，`failure_kind=parse_error`。
4. `llm_rate_limit`：最终 `retry_scheduled`，有 `next_retry_at`。
5. `fetch_failed`：最终不是 `done`，保留 typed error。
6. `output_failed`：最终不是 `done`，无假 output path。
7. `parallel_test_bundles`：可以跑 active + v2_legacy，并保留 prompt hash。
8. temporary state smoke：临时 `STATE_ROOT` 和临时 `queue.db`，不创建真实 `~/.100x_v3/queue.db`。

验收命令：

```bash
python -m pytest
python -m compileall src tests
```

再加一个 dry-run smoke，使用临时目录：

```bash
STATE_ROOT="$(mktemp -d)/.100x_v3" \
QUEUE_DB_PATH="$STATE_ROOT/queue.db" \
python -m pytest tests/test_pipeline_dry_run.py
```

## What Not To Build In Phase 2

明确不要做：

- 不启动 live daemon。
- 不启动 live scheduler。
- 不启动 live Telegram bot。
- 不连接 Telegram Bot API。
- 不写真实 Obsidian vault。
- 不读取 `~/.100x_v2/queue.db`。
- 不创建或写入真实 `~/.100x_v3/queue.db`。
- 不复制 V2 `.env`、cookie、token、config。
- 不实现复杂并发 worker。

## Phase 2 Implementation Order

推荐下一次开发按这个顺序推进：

1. 新增 `models.py`，统一结果模型和 `RuntimeMode`。
2. 新增 `prompt_parser.py`，先用单测锁定 schema 校验。
3. 新增 `llm/provider.py` stub，覆盖成功、parse、rate limit、timeout。
4. 新增 `fetchers/base.py` 和 `fetchers/fixture.py`。
5. 新增 `outputs/obsidian.py` 和 `outputs/telegram.py` staging/stub。
6. 新增 `pipeline.py`，先只跑 active bundle。
7. 接入 `PromptRegistry` 的 active bundle。
8. 增加 parallel bundle evaluation，但不影响 active output。
9. 完成六个核心 pipeline tests。
10. 跑 `pytest`、`compileall`、临时 state dry-run smoke。

## Possible Small Adjustment To Existing Docs

`docs/architecture.md` 的 Data Flow 写成：

```text
URL/RSS/IM input -> QueueStore -> RuntimeGuard -> Fetch ...
```

从 live 安全角度，更准确应是：

```text
RuntimeGuard -> Ingress -> QueueStore -> Pipeline -> Output
```

原因：任何 live role 在碰 queue 之前都应该先被 Runtime Guard 拦住。Phase 2 可以先不改旧文档，但下一次实现时应按这个顺序写代码。

## Next Session Starting Point

下一次核心开发可以直接从这条指令开始：

```text
请继续开发 knowledge-extractor/v3。先阅读 ai-reading/phase2-design-review-and-core-development-plan.md，然后按其中的 Phase 2 Implementation Order 实现 dry-run/staging 后端核心。不要启动 live daemon/scheduler/Telegram bot，不读取 V2 queue，不写真实 ~/.100x_v3/queue.db。完成后运行 python -m pytest、python -m compileall src tests，并用临时 STATE_ROOT/QUEUE_DB_PATH 跑 dry-run smoke。
```
