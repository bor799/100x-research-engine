# V3 发布交接与迁移状态

> 更新时间：2026-05-30  
> 当前结论：V3 产品代码已发布到草稿 PR，代码级验证通过；V2/Horizon 的生产退役仍需要 live-source-health 和现场运行验收。

---

## 1. 发布状态

目标仓库：

```text
https://github.com/bor799/information-tracking-agent
```

草稿 PR：

```text
https://github.com/bor799/information-tracking-agent/pull/1
```

发布分支：

```text
codex/publish-v3-latest
```

发布策略：

- 从远端 `main` 派生发布分支。
- 保留远端 README 公共文案。
- 同步 V3 产品目录、配置模板、脚本、源码、测试和 docs。
- 不把本地私有运行材料、缓存、密钥、`ai-reading/` 或测试 staging 输出带入发布分支。

---

## 2. 已完成能力

### Runtime 与队列

- `RuntimeGuard` 拒绝 V2 路径、`.100x_v2` 状态、shadow HOME、越界 queue path、以及不兼容 queue schema。
- `RuntimeGuard` 已修复公共仓库 checkout 名称问题：仓库目录可以叫 `information-tracking-agent`，不必叫 `v3`。
- `QueueStore` 支持 typed status、typed failure、retry scheduling、reply metadata、stale processing recovery 和 status counts。

### 配置与信息源

- `ConfigLoader` 支持 V3 原生配置、V2-compatible legacy config normalization、环境变量/path 展开。
- 默认自动加载 `config/sources.yaml`，并按 URL/name 去重。
- 2026-05-30 本地配置解析结果：`95` 个 RSS source。
- `config/config.example.yaml` 包含 runtime、live、LLM、outputs、daily reports、prompts、scheduler、worker、telegram bot、Agent Reach 示例。

### Pipeline 与输出

- Pipeline 已覆盖 fetch -> validate -> score -> extract -> telegram format -> output -> queue finalization。
- Prompt registry 支持 active bundle 和 parallel test bundles。
- Live/stub/shadow LLM providers 已存在。
- Staging/live Obsidian output 与 Telegram delivery 路径已存在。
- Worker 支持 once/loop、stale recovery、consecutive failure limit、staging/live mode。

### Scheduler、Telegram 与运维

- Scheduler 支持 source discovery 和入队，不在 scheduler 内执行 pipeline。
- Telegram inbound bot 支持 URL 入队、status/recent/failed/help 等交互路径。
- `scripts/run-v3.sh` 提供低层 operator commands。
- `scripts/control.sh` 是生产控制入口，支持 start/stop/recover/status/doctor/tmux/LaunchAgent/run-once/logs。
- Health monitor 会写 `~/.100x_v3/health.json` 并尝试恢复 stale roles。

### 多渠道与日报

- Fetcher router 和 Agent Reach multi-channel fetcher 已接入 worker 构建路径。
- 已有 web、RSS channel、search、social、fixture、multi-channel fetcher 模块。
- US AI market daily report runner 已实现：
  - `100x-v3-us-ai-daily`
  - `python -m knowledge_extractor_v3.daily_reports.runner`
  - watchlist/system file 初始化
  - recent V3 notes 和 stock context 读取
  - 交易周/category 输出路由
  - `manifest.jsonl` 与 signal ledger 幂等写入

---

## 3. 发布前验证

2026-05-30 在本地工作区和 clean publish checkout 中完成：

```bash
python -m compileall -q src tests scripts/v2_compare.py scripts/test_rss_fetch.py
bash -n scripts/*.sh
python -m pytest -q
```

结果：

- Python 编译检查：通过。
- Shell 语法检查：通过。
- 单测：`259 passed`。
- 私有文件扫描：未发现 `.env`、`config/config.local.yaml`、`.DS_Store`、cache、bytecode。
- 常见 token pattern 扫描：未发现 GitHub/OpenAI/Zhipu 风格 token。
- README diff check：远端 README 公共文案未被发布分支改动。

RSS live fetch 验证：

- `python scripts/test_rss_fetch.py --all --timeout 8 --target-rate 95` 曾长时间无输出。
- Operator 决策为跳过本次发布的 RSS live fetch。
- 该项仍是 source-health gate，不是单测失败。

---

## 4. 仍未完成的生产迁移门槛

V3 代码发布不等于 V2/Horizon 已可退役。退役前必须完成：

- `scripts/control.sh doctor` 无 error/critical。
- V3 nightly 连续 2 次成功。
- 手动 URL 入队、worker 处理、Obsidian 输出、Telegram 回执端到端成功。
- RSS live fetch 成功率大于等于 95%，或失败源被记录为明确的 source-health 风险。
- Horizon-like daily brief/profile 端到端成功，或明确决定不再保留 Horizon 等价能力。
- `ps` 和 crontab 中确认无仍需保留的 V2/Horizon 生产任务，再执行退役。

---

## 5. 下一步操作清单

1. 在 PR #1 上审查 GitHub diff，确认没有私有文件或 README 误改。
2. 在真实网络环境重新跑 RSS live fetch：

   ```bash
   python scripts/test_rss_fetch.py --all --timeout 8 --target-rate 95
   ```

3. 运行生产前 doctor：

   ```bash
   scripts/control.sh doctor
   ```

4. 做一次 staging run：

   ```bash
   scripts/control.sh run-once 20 10
   ```

5. 跑日报 dry run：

   ```bash
   python -m knowledge_extractor_v3.daily_reports.runner --dry-run
   ```

6. PR 合并后，再按 `docs/V3_AUTORUN_OPERATIONS.md` 检查 live role、cron/tmux/LaunchAgent 和 V2/Horizon 退役条件。

---

## 6. 相关文档

- [Architecture](architecture.md)
- [V3 Autorun Operations](V3_AUTORUN_OPERATIONS.md)
- [V3 24x7 ST/SOP](V3_24X7_ST_SOP.md)
- [RSS Fetch Architecture Analysis](RSS_FETCH_ARCHITECTURE_ANALYSIS.md)
- [V2 to V3 Migration Plan](V2_TO_V3_MIGRATION_PLAN.md)
