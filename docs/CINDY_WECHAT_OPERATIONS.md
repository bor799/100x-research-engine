# 100X → Cindy → 微信：轻量生产架构

## 第一性原理

100X 的职责只有两个：按计划生产内容，以及把一个不可变事件写入持久化 Outbox。Cindy 的职责只有两个：持有已登录的微信身份，以及在绑定会话中执行发送工具。两边通过 JSON 状态机交接，不共享数据库、token 或内部实现。

```text
RSS / 手动 URL
      ↓
100X scheduler + worker
      ↓
pending → processing → sent
   ↑         ↘ nack（内容失败，最多 3 次）→ failed
   │         ↘ release（通道故障，不烧 attempt）
   └────────── requeue-failed（通道恢复后补发，attempt 归零）
      ↑
Cindy bound scheduled agent → cindy_wechat → 个人微信
```

“轻”不是删掉可靠性，而是把复杂度放在正确的所有者处：100X 不实现微信登录和联系人系统，Cindy 不理解评分、RSS 或内容生产。正常运行不需要 Murphy 参与；只有 `failed/` 非空或 Cindy task 失败时才需要处理。

## 生产契约

- 默认生产输出为 `wechat`；Telegram delivery 与 Telegram bot 均关闭，但代码保留以便显式回滚。
- Cindy 生产 task 必须绑定一个已验证的 WeChat-origin session。禁止依赖“最近联系人”。
- Cindy 只发送 claim 返回的 `text`，不重写内容。
- `event_id` 由永久 idempotency marker 去重；即使 sent ledger 被清理，同一事件也不能重放。
- attempt 记账：claim +1；ack/nack 保持；release -1（通道故障不消耗）；stale 回收同 release；`requeue-failed` 归零并刷新 TTL。第三次内容失败（nack）进入 `failed/`。
- 失败分级：通道故障（SEND_FAIL、含短探针全挂）→ `release` 回 pending 等恢复；内容级失败（单条挂、其余正常）→ `nack` 烧重试。
- recipient/session 只保留 SHA-256 引用。Connector 不提供某字段时保持空值并写入 `observability_gaps`，不猜测。
- 不直接修改 Cindy 数据库，不把 raw peer/session/context token 写入仓库或 prompt。

## Cindy 任务

生产保留两个既有节奏（实际为三个 Cindy schedule：商业早/午档、商业傍晚档、战略周日档），共用同一个两段式消费者协议（cron 与 prompt 由 Cindy schedule 持有，以实际配置为准）：

| Task | 节奏 | Lane | 每轮上限 | Session |
|---|---|---|---:|---|
| 100X 商业故事推送（早/午档） | 每天 8:30 / 11:30 | business | 1 + 7 | 同一个 verified bound session |
| 100X 商业故事推送（傍晚档） | 每天 17:20 | business | 1 + 7 | 同一个 verified bound session |
| 100X 战略观察周报 | 每周日 17:20 | strategic | 1 + 4 | 同一个 verified bound session |

两段式领取：先 `claim --limit 1` 发送首条作为通道探针（发真实卡片，不发测试消息）；成功后再领余量逐条发送。首条即 SEND_FAIL 判定通道故障：对全部已 claim 条目 `release`（不烧 attempt）、调用 `schedule_notify_current_run` 提醒重新登录微信、结束本轮。首条成功后个别失败视为内容级问题，走 `nack`。

三个 task 分别使用：

- `scripts/schedule-checks/100x.mjs`（商业早/午档）
- `scripts/schedule-checks/100x-2.mjs`（战略周日档）
- `scripts/schedule-checks/100x-3.mjs`（商业傍晚档）

Pre-run hook 先 best-effort 运行 `wechat_outbox.py recover --stale-seconds 14400` 回收卡死的 processing 条目（4h 阈值避开最长历史 run），再检查 Outbox：空时以 code 2 静默跳过，避免无意义地启动 agent。

## 微信交互入口

用户在已绑定的 Cindy 微信会话中发 URL 或询问状态时，Cindy 只需调用确定性 CLI：

```bash
python scripts/cindy_control.py enqueue-url "https://example.com/article"
python scripts/cindy_control.py status
python scripts/cindy_control.py status --task-id 123
```

CLI 输出 JSON，Cindy 可以原样压缩成微信回复。它不接触 connector 身份，也不需要 Telegram。

## 本地落档（推送账本）

每条实际送达微信的卡片在 `ack` 的同时会落进 vault 的周滚动账本，不依赖微信聊天记录：

- 位置：`<obsidian_root>/YYYY-MM-WN/微信推送 YYYY-MM-WN.md`，直接放在用户阅读的周文件夹内（如 `信息源/2026-08-W4/微信推送 2026-08-W4.md`，与《一周关注简报》同目录、同命名样式）。周编号为 month-week 约定 `W = ceil(day/7)`（W1..W5）；周文件夹不存在时自动创建（用户 2026-08-26 已确认由管道持续延长周序列）。
- 条目：发送时间、车道、final_score、原文链接、卡片全文（blockquote），并按 `event_id = content_hash` 互链 `AI进展/` 下的完整萃取笔记（`[[...]]`）。
- 幂等：按 `event_id` 去重，重复 ack / 回填不会产生重复条目。
- 隔离：`ack` 失败不影响落档，反之落档失败只打 stderr 警告——outbox 回执仍是送达事实的唯一来源。sent ledger 14 天后清理，账本是更长期的本地记录。
- 沙箱安全：`--queue-dir` 覆盖队列时（测试/演练）不解析真实 config、不写真实 vault；除非显式设 `PUSH_LEDGER_DIR`。

历史回填（一次性或补账）：`python3 scripts/wechat_outbox.py land`——扫描 `sent/` 全部落档，输出 `{"landed": N, "duplicates": N, ...}`，无可落条目时 exit 2。frontmatter 侧同步：push 车道条目的 `AI进展` 笔记自 2026-08-26 起带 `wechat_lane` 字段，可与 archive-only 内容区分。

## 通道掉线 runbook

已知故障模式：Cindy 微信连接器的 iLink 会话每隔几天被微信侧静默失效，所有发送（含短探针）返回 SEND_FAIL，Cindy 日志出现 `iLink rejected the message`，且无任何 logout/kick 事件。2026-08-18/19/20/22 均发生过。

处置步骤：

1. **确认症状**：`python scripts/wechat_outbox.py status` 中 `failed` 增长、微信收不到卡片；`grep "iLink rejected" ~/Library/Application\ Support/Cindy/logs/main-*.log` 有当日记录。
2. **重新登录**：在 Cindy 中重新登录个人微信（唯一能恢复通道的动作）。
3. **验证恢复**：从 Cindy 手动发一条微信消息成功，且日志不再新增 iLink 拒绝。
4. **补发欠账**：`python3 scripts/wechat_outbox.py requeue-failed --reason "<掉线时段>; WeChat re-login completed"`——投递失败条目重回 pending（attempt 归零、TTL 刷新），质检拦截条目默认跳过。
5. **确认**：`status` 中 `failed` 只剩质检拦截项，`pending` 等待下一推送窗口（或对傍晚档 `schedule_run_now` 立即触发）。

两段式领取生效后，掉线期间的条目只会被 release 保全并触发桌面通知，不再烧成 failed；本 runbook 主要用于历史欠账与通知未被及时处理的场景。

## 验证与日常检查

```bash
scripts/control.sh start-tmux
python -m pytest -q tests/test_wechat_outbox.py tests/test_wechat_queue.py tests/test_cindy_control.py tests/test_push_ledger.py
python scripts/wechat_outbox.py status
python scripts/cindy_control.py status
scripts/control.sh status
```

当前仓库位于 macOS 受保护的 `Documents` 目录。没有额外隐私授权时，LaunchAgent 会以 exit 126 被 TCC 拒绝访问工作目录；生产因此使用单实例 tmux 控制面。不要复制一份代码到非受保护目录规避这个限制。若需要重启后自动拉起，应先明确授予对应 shell/launchd 访问权限，再运行 `scripts/control.sh install-autostart` 并核验 `launchctl list`，否则保持 tmux 模式。

上线验收：

1. fixture 完成 `pending → processing → sent`，receipt 有消息 ID、时间和哈希引用；
2. 重放 fixture 返回 duplicate，不调用微信；
3. 三次 nack 后进入 failed，三个 receipt 均保留；
4. Cindy bound scheduled task 在无新微信入站时仍能发出；
5. 下一条自然 100X Brief 手机实收且 sent ledger 对账；
6. `scripts/control.sh status` 中 Telegram bot 为 disabled，scheduler/worker/health 正常。

如果第 4 条失败，结论是 Cindy connector 的无人值守能力阻断；不应再修改 100X 内容生产面。
