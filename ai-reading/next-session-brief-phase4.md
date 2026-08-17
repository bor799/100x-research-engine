# Phase 4 Core Development Brief

Created: 2026-04-28

> 2026-05-30：本文件已归档为 Phase 4 历史开发 brief，不是当前接手入口。当前入口见项目根 `AGENTS.md` 与 `docs/MIGRATION_COMPLETE.md`。

**目标**：完成 Phase 4C-4H，使 V3 可以真正运行定时抓取、LLM 萃取、Obsidian 写入、Telegram 推送。

---

## 前置条件检查

开始前确认：
- `python -m pytest` 全部通过
- `python -m compileall src tests` 无错误
- Phase 4A 已完成（ConfigLoader、LiveGate、LiveObsidianWriter、LiveTelegramClient）

---

## 开发路由（按顺序执行）

### 4C: Real LLM Provider

**新增文件**：
- `src/knowledge_extractor_v3/llm/live_provider.py`

**职责**：
- 实现 `LLMProvider` 协议
- 从环境变量读取 API Key
- 支持 zhipu/anthropic/openai 等路由
- 处理 rate limit → `retry_scheduled`
- 处理 timeout → `retry_scheduled`
- 处理 parse error → `failed_terminal`
- 保存原始 LLM 响应到 `raw_text`

**测试**：
- `tests/test_live_llm_provider.py` — 用 fake HTTP transport 测试

**验收**：
- rate limit 映射正确
- timeout 映射正确
- parse error 映射正确
- 无 secret 泄漏到日志

---

### 4D: Queue Worker

**新增文件**：
- `src/knowledge_extractor_v3/worker.py`

**职责**：
- Runtime Guard 启动检查
- 恢复 stale `processing` 任务
- 按优先级读取 ready tasks
- 处理每个 task through pipeline
- 尊重 `batch_size`
- 连续失败达到上限时停止
- JSONL 日志记录
- SIGINT/SIGTERM 优雅退出

**QueueStore 扩展**（不破坏现有 schema）：
```python
QueueStore.next_ready_tasks(limit: int, now: str) -> list[QueueTask]
QueueStore.recover_stale_processing(before: str) -> int
QueueStore.count_by_status() -> dict[str, int]
QueueStore.find_by_url(url: str) -> QueueTask | None
```

**测试**：
- `tests/test_worker.py` — 用临时 queue.db 测试

**验收**：
- `worker-once --limit 5` 处理任务
- failed fetch → 不 done
- output failure → 不 done
- stale processing 可恢复
- SIGTERM 优雅退出

---

### 4E: Source Registry + Scheduler

**新增目录**：
- `src/knowledge_extractor_v3/sources/`

**新增文件**：
- `sources/models.py` — SourceItem, SourceConfig
- `sources/registry.py` — 加载和验证 source config
- `sources/rss.py` — RSS adapter
- `sources/url_list.py` — URL list adapter
- `sources/dedupe.py` — URL 去重
- `src/knowledge_extractor_v3/scheduler.py`

**职责**：
- Runtime Guard 启动检查
- 加载 source registry
- 发现 source items（RSS、url_list）
- 只入队，不调用 pipeline
- 应用 source priority
- 强制 per-source 和 per-tick 限制
- 写 scheduler event log

**测试**：
- `tests/test_source_registry.py`
- `tests/test_scheduler_rss.py`

**验收**：
- RSS 日期解析支持 RFC822 和 ISO 8601
- lookback window 强制执行
- 重复 URL 不会创建重复任务
- scheduler 不调用 pipeline
- scheduler 默认禁用

---

### 4F: Telegram Inbound Bot

**新增文件**：
- `src/knowledge_extractor_v3/telegram_bot.py`

**职责**：
- Runtime Guard 启动检查
- 从 env 读取 token
- 只接受来自 allowed chat ids 的消息
- 入队 URL（高 priority）
- 设置 `reply_channel="telegram"` 和 `reply_chat_id`
- 回复 queue id 和初始状态
- 不在 bot 内处理 task

**Bot 命令**：
```
/url <url>
/status <queue_id>
/recent
/failed
/help
```

**测试**：
- `tests/test_telegram_bot.py` — mock HTTP

**验收**：
- 未知 chat 被拒绝
- 有效 URL 创建 queue task
- bot 不内联处理 task
- reply chat id 被保留

---

### 4G: Operator Commands

**新增文件**：
- `scripts/run-v3.sh`

**必需命令**：
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

**安全**：
- `live-*` 命令拒绝，除非：
  - `config/config.local.yaml` 存在
  - `live.enabled: true`
  - required env vars 存在
  - Runtime Guard 通过

**测试**：
- `tests/test_run_v3_script.py`

---

### 4H: Health Check

**新增文件**：
- `src/knowledge_extractor_v3/health.py`

**检查项**：
- V3 path isolation
- Queue schema
- Queue status counts
- Stale `processing` tasks
- Role lock freshness
- Recent scheduler tick
- Recent worker task result
- Telegram token presence（如果启用）
- Obsidian root 可写（如果启用）
- Source config 可解析
- Prompt registry 有效

**恢复**：
- stale `processing` → `retry_scheduled`
- broken role lock → 标记 stale（不 kill）

---

## 最终验收标准

Phase 4 完成时：
- [ ] `run-v3.sh preflight` 通过
- [ ] live mode 默认拒绝
- [ ] live Obsidian output 写入正确 markdown
- [ ] Telegram 可安全启用/禁用
- [ ] real LLM provider 正确映射失败
- [ ] scheduler 可入队但不处理
- [ ] worker 可处理 pending/retry 任务
- [ ] Telegram bot 可入队手动 URL
- [ ] health check 报告队列状态
- [ ] 不读取 V2 queue/config/secrets
- [ ] 无失败路径标记为 done
- [ ] rollback 可停止 live roles

---

## V2 → V3 版本迁移（Phase 4 完成后执行）

在 Phase 4 开发完成并通过测试后，执行版本迁移：

**前置阅读**：
1. `ai-reading/v2-to-v3-migration-plan.md`

**迁移步骤**（按顺序）：

1. **准备工作**
   - 停止 V2 运行
   - 备份 V2 `config/config.yaml`
   - 备份 V2 队列数据（如有）

2. **创建 V3 .env**
   - 从 V2 获取 Telegram Bot Token
   - 从 V2 获取 Chat ID
   - 从环境或 V2 获取 Zhipu API Key

3. **移植 Agent Reach**
   - 创建 `fetchers/multi_channel.py`
   - 复用 V2 的 Agent Reach 逻辑
   - 输出符合 V3 的 `FetchedContent` 格式

4. **转换信息源配置**
   - 将 V2 的 70+ RSS 源转换为 V3 格式
   - 写入 V3 `config.local.yaml`

5. **验证测试**
   - 启动 V3 Telegram Bot 接收测试
   - 发送各渠道测试链接验证抓取
   - 验证 Obsidian 输出正确
   - 验证 Prompt 切换功能

6. **切换完成**
   - V3 设为生产运行
   - V2 归档备份

---

## 给下一个 Codex Session 的核心指令

```text
请继续开发 knowledge-extractor/v3，完成 Phase 4 剩余部分（4C-4H）。

前置阅读：
1. ai-reading/phase4-live-backend-architecture-and-production-rollout-plan.md
2. ai-reading/next-session-brief-phase4.md
3. ai-reading/v2-to-v3-migration-plan.md（迁移参考，Phase 4 完成后执行）

开发原则：
1. 按 4C → 4D → 4E → 4F → 4G → 4H 顺序执行
2. 每个阶段完成后运行 pytest 和 compileall 验证
3. live.enabled 默认为 false
4. 不启动自动 loop roles（除非测试明确需要）
5. 不读取 V2 queue/config/secrets
6. 测试中使用临时 STATE_ROOT 和 OBSIDIAN_ROOT

每个阶段完成后：
- 运行 python -m pytest
- 运行 python -m compileall src tests
- 确认所有现有测试仍然通过
- 确认新测试覆盖关键路径
```

---

## 关键文件依赖关系

```
4C: llm/live_provider.py
    → 依赖: models.py, llm/__init__.py
    → 被: 4D worker.py 使用

4D: worker.py
    → 依赖: llm/live_provider.py, queue_store.py, pipeline.py
    → 扩展: queue_store.py（helper 方法）

4E: sources/ + scheduler.py
    → 依赖: queue_store.py, sources/models.py
    → 独立: 不依赖 worker.py

4F: telegram_bot.py
    → 依赖: queue_store.py, sources/models.py
    → 独立: 不依赖 worker.py 或 scheduler.py

4G: scripts/run-v3.sh
    → 依赖: 所有上述模块

4H: health.py
    → 依赖: 所有上述模块
```

---

## 禁止事项

- 不在实现 X/WeChat live login 前，RSS/url_list loop 必须先稳定
- 不自动启动 loop roles
- 不在 tracked config 中启用 live.enabled
- 不提交 local secrets
- 不复制 V2 .env, cookies, config, queue DB
- 不将 Telegram failure 标记为 done
- 不让 scheduler 直接调用 pipeline
- 不使用 broad pkill 命令
