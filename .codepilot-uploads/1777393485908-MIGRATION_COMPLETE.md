# V2 / Horizon -> V3 迁移复核与执行计划

> 复核时间：2026-04-28  
> 当前结论：迁移尚未完成。V3 已有核心骨架，但还不能替代 V2 与 Horizon News。  
> 目标状态：V3 成为唯一执行系统；V2、Horizon 及旧机器人/cron 只保留为参考或归档，不再运行。

---

## 1. 这次迁移的真实目标

这次不是把 V2 或 Horizon 的代码搬进 V3，而是把它们的有效能力用 V3 的架构重新实现：

- V3 是 V2 的升级版，后续所有任务都应进入 V3 队列、V3 worker、V3 输出链路。
- V2/旧机器人/Horizon cron 应在 V3 替代能力验证通过后关闭。
- Horizon News 应作为 V3 的一个独立信息源/profile，而不是继续保留一套独立日报机器人。
- 不迁移旧系统的技术债：影子状态目录、broad `pkill`、硬编码 token、启动时 `git pull`/`uv sync`、V2 queue 依赖、直接 `sys.path` 引入 V2 代码。

---

## 2. 当前事实盘点

### 2.1 V3 已有能力

- `QueueStore` 已有独立 SQLite 队列、typed status、retry state、reply metadata 字段。
- `RuntimeGuard` 已能拒绝明显的 V2 路径与状态污染。
- `Pipeline` 已有 fetch -> validate -> prompt -> score -> extract -> telegram format -> output 的顺序链路。
- `Worker` 已有 once/loop、stale processing recovery、consecutive failure limit。
- `Scheduler` 已有 source registry、RSS/url_list discovery、入队能力雏形。
- `LiveLLMProvider` 已有 provider routing 与 HTTP error -> typed failure 映射。
- `LiveObsidianWriter` 与 `LiveTelegramClient` 已有真实输出口。
- `TelegramInboundBot` 已有 `/url` 入队和 `/status` 等基础命令。
- `config/config.local.yaml` 当前启用了 live、scheduler、telegram bot，配置了 39 个 RSS 源。

### 2.2 当前验证结果

- `python -m compileall src tests`：通过。
- `python -m pytest`：163 passed, 1 failed。
  - 失败点：`tests/test_worker.py::test_worker_signal_handler_sets_shutdown_flag` 缺少 `signal` import，属于测试文件小问题。
- `bash scripts/run-v3.sh preflight`：通过。
- `./scripts/run-v3.sh preflight`：失败，因为 `scripts/run-v3.sh` 不可执行。
- `bash scripts/run.sh rss` / `bash scripts/run.sh worker`：失败，因为脚本未设置 `PYTHONPATH`，并且 `rss` 调用了不存在的 `knowledge_extractor_v3.sources` CLI。
- `create_scheduler(config)`：失败，`Path.with_suffix("-scheduler.jsonl")` 是非法 suffix。
- `knowledge_extractor_v3.fetchers.multi_channel`：导入失败，`ModuleNotFoundError: No module named 'fetchers'`。当前 V3 的 multi-channel 并没有真正可用。

### 2.3 当前运行环境风险

- 当前 crontab 仍有 Horizon News：
  - `10 5 * * * ~/Library/Application\ Support/nanobot/cron/horizon_bash.sh >> /tmp/horizon_daily.log 2>&1`
- 当前 crontab 同时已有 V3 nightly：
  - `30 5 * * * .../knowledge-extractor/v3/scripts/nightly_job.sh`
- 进程列表中发现仍有 V2 queue worker：
  - `QUEUE_DB_PATH=/Users/murphy/.100x_v2/queue.db .venv/bin/python -m src.main --queue --limit 3`
- 因此现在不是“V3 已接管”，而是“V2/Horizon/V3 并存且有分裂风险”。

### 2.4 信息源差距

- V2 `config/config.yaml`：91 个启用 RSS 源。
- V3 `config/config.local.yaml`：39 个启用 RSS 源。
- V3 `config/sources.yaml`：已有 91 个 RSS 源备份，但当前没有被 config loader 自动加载。
- Horizon 当前信息源类型包括：
  - GitHub user events / repo releases
  - Hacker News top stories + comments
  - RSS
  - Reddit subreddit/user + comments
  - Telegram public channel

---

## 3. 不应该迁移的旧东西

这些能力不应该原样带进 V3：

- V2 的 Central Commander 子进程模型。
- V2/Horizon 的 broad `pkill` 停进程方式。
- V2 queue、V2 state root、V2 lock 文件。
- V3 `multi_channel.py` 当前这种 `sys.path` 指向 V2 的做法。
- Horizon `daily-run-batch.sh` 里的运行时 `git pull`、`uv sync`、gh-pages deploy、token fallback。
- Horizon 的整套日报仓库作为生产执行器。
- V2 的 prompt 作为主 prompt；最多保留为对照 bundle。

---

## 4. 必须迁移的能力

### P0：先让 V3 可运行

1. 统一入口脚本
   - 保留一个主入口：建议 `scripts/run-v3.sh` 或新增 package CLI。
   - 修复可执行权限。
   - 所有命令设置稳定 `PYTHONPATH` 或使用已安装 package。
   - 命令至少支持：`preflight`、`status`、`enqueue-url`、`scheduler-once`、`worker-once`、`worker-loop`、`bot-loop`、`health`、`nightly`。
   - 移除 `scripts/run.sh` 里的错误命令或让它转发到统一入口。

2. 修复基础红灯
   - 修复 `tests/test_worker.py` 缺 `signal` import。
   - 修复 `scheduler.py` 的 `with_suffix("-scheduler.jsonl")`。
   - 确认 `python -m knowledge_extractor_v3.scheduler --once --limit N` 能启动。
   - 确认 `python -m knowledge_extractor_v3.worker --once --limit N --mode staging/live` 能启动。

3. 修复 nightly job
   - 不再调用 `knowledge_extractor_v3.sources rss`。
   - 应改为：scheduler 入队 -> worker 处理 -> health/status 汇总 -> Telegram admin summary。

### P0：补齐 V2 核心链路

1. Source config loader
   - 支持从 `config/sources.yaml` 或 `sources_files` 加载外部信息源。
   - 保留并解析：`category`、`priority`、`tags`、`cron_interval`、`lookback_days`、`max_items`、`metadata`。
   - 将 V2 的 91 个 RSS 源纳入 V3 正式配置，而不是只保留 39 个。

2. Scheduler
   - 支持每个 source/profile 独立调度。
   - 记录 source 上次成功 discovery 时间。
   - 入队时保留 source metadata，至少包括 title、published_at、author、source_type、category/tags。
   - 只负责发现和入队，不在 scheduler 内处理正文萃取。

3. RSS adapter
   - 使用稳定 parser 或增强现有 parser，支持 RSS/Atom 常见命名空间。
   - 修复 naive/aware datetime 比较问题。
   - 支持 RFC822、ISO 8601、date-only。
   - 支持 env var 展开，例如 Horizon LWN feed URL。
   - 解析失败要记录 source warning，不要静默吞掉。

4. Fetcher router
   - Worker 不应只有 `WebPageFetcher`。
   - 根据 URL/domain/source_type 选择 fetcher：
     - normal web -> `WebPageFetcher`
     - X/Twitter、YouTube、微信、小宇宙 -> V3 native multi-channel fetcher
     - Telegram public channel item -> direct content 或 message fetcher
     - fixture -> `FixtureFetcher`

5. Agent Reach / 多渠道抓取
   - 删除 `sys.path` 依赖 V2 的实现。
   - 将需要的 channel 以 V3 adapter 方式重写或干净 vendoring：
     - Twitter/X
     - YouTube
     - 微信公众号
     - 小宇宙播客
     - Jina/Web fallback
   - 所有错误返回 V3 `TypedError`，不要 print 后吞掉。
   - 增加 health check：auth invalid、cookie 过期、外部 CLI missing、timeout。

### P0：Horizon News 迁移为 V3 profile

Horizon 不再是独立机器人。它应该变成 V3 的一个 source profile，例如：

```yaml
source_profiles:
  horizon_daily:
    enabled: true
    schedule: "daily"
    lookback_hours: 8
    output_mode: "daily_brief"
    sources:
      - type: hackernews
      - type: github
      - type: reddit
      - type: telegram_public_channel
      - type: rss
```

需要实现的 V3 source adapters：

- `hackernews`：top stories + top comments。
- `github`：user events、repo releases。
- `reddit`：subreddits/users + top comments。
- `telegram_public_channel`：公开频道 web preview 抓取。
- `rss`：复用 V3 RSS adapter。

Horizon 的日报输出也要拆成 V3 能力：

- 入队/处理每条候选新闻。
- 按 profile 聚合当天高分结果。
- 生成一条 Telegram daily brief。
- Obsidian 输出到独立目录，例如 `信息源/Horizon News/` 或 `信息源/AI进展/日报/`。

### P1：Telegram 交互闭环

- V3 Bot 现在只支持 `/url <url>`，V2 支持更自然的“直接发 URL 入队”。应恢复直接 URL 识别。
- Worker 输出时应优先回复 `reply_chat_id`，而不是只发给 admin chat。
- 失败回执要包含 failure_kind、next_action、retry time。
- daemon/bot 异常退出要向 admin 告警。
- Telegram 409 conflict 不应直接让 bot 静默退出。

### P1：LLM 稳定性

- `LiveLLMProvider` 当前配置里有 `max_retries`，但实际没有 retry loop。
- 需要实现：
  - 429 / 1308 / 1302 熔断。
  - timeout 指数退避重试。
  - `retry_after` 感知。
  - 全局节流，避免 scheduler + manual URL 同时打爆额度。

---

## 5. 推荐执行顺序

### Phase 0：冻结旧系统，先不切断

目标：防止继续扩散，但不立刻断生产。

- 记录当前 crontab 与运行进程。
- 不再新增 V2/Horizon 任务。
- 准备 `scripts/decommission_old.sh`，但先不执行。
- 明确切换标准：V3 nightly + manual URL + Horizon profile 连续成功后，才关闭旧 cron/进程。

### Phase 1：修 V3 启动面

完成标准：

- `python -m pytest` 全绿。
- `bash scripts/run-v3.sh preflight` 通过。
- `scripts/run-v3.sh` 可直接执行。
- `scripts/run-v3.sh scheduler-once --limit 1` 可运行。
- `scripts/run-v3.sh worker-once --limit 1 --mode staging` 可运行。
- `scripts/nightly_job.sh` 不再调用错误模块。

### Phase 2：把 V2 RSS 全量接入 V3

完成标准：

- V3 正式配置能加载 91 个 V2 RSS 源。
- source metadata 不丢失。
- scheduler 可按 lookback/interval 入队。
- RSS date parser 覆盖 V2 的 O4/O6 问题。

### Phase 3：实现 V3 native fetcher router

完成标准：

- 普通网页、RSS item、X/Twitter、YouTube、微信、小宇宙都有 V3 fetcher 路径。
- `multi_channel.py` 不再引用 V2 路径。
- Worker 默认使用 fetcher router。
- 多渠道失败会进入 typed retry/manual_review，而不是静默失败。

### Phase 4：把 Horizon News 做成 V3 profile

完成标准：

- V3 有 `horizon_daily` profile。
- Hacker News / GitHub / Reddit / Telegram public channel / RSS 能 discovery 入队。
- 支持 8 小时 lookback。
- 生成每日 Telegram brief。
- 不再依赖 `/Users/murphy/Documents/Horizon/scripts/daily-run-batch.sh`。

### Phase 5：切换与废弃

切换前检查：

- V3 nightly 连续 2 次成功。
- Telegram 手动 URL 端到端成功。
- Horizon profile 端到端成功。
- Obsidian 输出与 Telegram 通知都正常。
- Health check 无 critical。

切换动作：

- 删除/注释 Horizon cron。
- 停止 V2 worker/bot/scheduler。
- 确认 `ps` 中无 V2/Horizon 长驻任务。
- 保留 `../v2` 与 `/Users/murphy/Documents/Horizon` 为只读参考，必要时重命名为 deprecated。
- 更新 nanobot/外部机器人指令，所有链接只入 V3。

---

## 6. 最终验收清单

- [ ] V3 是唯一 cron 执行器。
- [ ] crontab 不再包含 Horizon 或 V2 任务。
- [ ] 无 V2 queue worker / bot / scheduler 进程。
- [ ] V3 加载完整 V2 RSS 源。
- [ ] Horizon News 作为 V3 source profile 运行。
- [ ] Telegram 直接发 URL 能入队并回执。
- [ ] X/Twitter、YouTube、微信、小宇宙可通过 V3 fetcher router 抓取。
- [ ] LLM 429/timeout 会进入 retry/circuit breaker。
- [ ] Obsidian 输出正常。
- [ ] Telegram admin summary 正常。
- [ ] `python -m pytest` 全绿。
- [ ] `docs/MIGRATION_COMPLETE.md` 被更新为真正的完成报告，而不是计划。

---

## 7. 给下一 session 的第一批任务

建议下一 session 不要先动 Horizon，也不要先关旧机器人。先把 V3 跑起来：

1. 修复 `tests/test_worker.py` 的 `signal` import。
2. 修复 `src/knowledge_extractor_v3/scheduler.py` 的 log path suffix。
3. 修复 `scripts/run-v3.sh` 权限与 `scripts/run.sh` 的错误命令。
4. 修复 `scripts/nightly_job.sh`，改为 scheduler + worker。
5. 增加 source config 外部文件加载，把 `config/sources.yaml` 的 91 个 RSS 源纳入正式配置。
6. 增加一个最小 smoke test：
   - scheduler 从临时 RSS feed 入队。
   - worker 用 fixture/web fetcher 处理。
   - output 写 staging。
7. 然后再做 native multi-channel fetcher 与 Horizon profile。

这份文件当前不是完成报告，而是迁移复核与执行计划。等 Phase 5 完成后，再把它改写成真正的迁移完成报告。
