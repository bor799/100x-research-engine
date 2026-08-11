# 100X → Cindy → 微信：轻量生产架构

## 第一性原理

100X 的职责只有两个：按计划生产内容，以及把一个不可变事件写入持久化 Outbox。Cindy 的职责只有两个：持有已登录的微信身份，以及在绑定会话中执行发送工具。两边通过 JSON 状态机交接，不共享数据库、token 或内部实现。

```text
RSS / 手动 URL
      ↓
100X scheduler + worker
      ↓
pending → processing → sent
                 ↘ retry (最多 3 次) → failed
      ↑
Cindy bound scheduled agent → cindy_wechat → 个人微信
```

“轻”不是删掉可靠性，而是把复杂度放在正确的所有者处：100X 不实现微信登录和联系人系统，Cindy 不理解评分、RSS 或内容生产。正常运行不需要 Murphy 参与；只有 `failed/` 非空或 Cindy task 失败时才需要处理。

## 用户只看见两个动作

1. Murphy 把文章链接、正文、笔记或问题发给 Cindy。
2. Murphy 在同一个微信入口阅读固定模板结果。

Cindy 使用 [固定 interaction contract](../prompts/cindy_wechat_interaction.md)。URL
进入 100X 异步处理；纯文本和问题由当前 Cindy 会话直接回答。用户主动提交的 URL
属于“请求解读”，不再受 RSS/自动发现的推送筛选门槛影响；评分仍保留为分析证据，
但不能把明确的用户请求静默丢弃。任务 ID、队列、模型、receipt 和 retry 都是隐藏实现。

## 生产契约

- 默认生产输出为 `wechat`；Telegram delivery 与 Telegram bot 均关闭，但代码保留以便显式回滚。
- Cindy 生产 task 必须绑定一个已验证的 WeChat-origin session。禁止依赖“最近联系人”。
- Cindy 只发送 claim 返回的 `text`，不重写内容。
- `event_id` 由永久 idempotency marker 去重；即使 sent ledger 被清理，同一事件也不能重放。
- claim 开始一次 attempt；ack/nack 写入时间线和脱敏 receipt。明确的连接器失败最多尝试三次。
- stale processing 的发送结果不确定，必须直接隔离到 `failed/`，禁止自动重发；否则“微信已发送、ack 尚未落盘”的崩溃窗口会产生重复消息。
- recipient/session 只保留 SHA-256 引用。Connector 不提供某字段时保持空值并写入 `observability_gaps`，不猜测。
- 不直接修改 Cindy 数据库，不把 raw peer/session/context token 写入仓库或 prompt。

## Cindy 任务

生产保留两个既有节奏，并增加一个对用户不可见的快速请求通道；三者共用同一个消费者协议：

| Task | Cron | Lane | 最大条数 | Session |
|---|---:|---|---:|---|
| 100X 请求解读 | `*/2 * * * *` | requested | 2 | 同一个 verified bound session |
| 100X 商业故事推送 | `30 8,18 * * *` | business | 2 | 同一个 verified bound session |
| 100X 战略观察周报 | 保留现有周报 cron | strategic | 2 | 同一个 verified bound session |

两个 task 使用 [固定 delivery prompt](../prompts/cindy_wechat_delivery.md)，并分别使用：

- `scripts/schedule-checks/100x.mjs`
- `scripts/schedule-checks/100x-2.mjs`
- `scripts/schedule-checks/100x-requested.mjs`

Pre-run hook 在 Outbox 为空时以 code 2 静默跳过，避免无意义地启动 agent。
`requested` 通道只处理 Cindy 明确提交的 URL，避免用户请求等待到每天两次的自动推送时点。

## 微信交互入口

用户在已绑定的 Cindy 微信会话中发 URL 或询问状态时，Cindy 只需调用确定性 CLI：

```bash
python scripts/cindy_control.py enqueue-url "https://example.com/article"
python scripts/cindy_control.py status
python scripts/cindy_control.py status --task-id 123
```

CLI 输出 JSON，Cindy 可以原样压缩成微信回复。它不接触 connector 身份，也不需要 Telegram。
入队成功的固定用户回复是“已收到。会按固定模板处理，完成后从当前微信入口发回。”；
最终失败只返回一次可理解的原因和下一步，不暴露内部 stage 或 error stack。

## 必须改造与不应改造

必须改造的是用户请求语义、Cindy 入站契约、生产 schedule 的 session/receipt
协议，以及未知发送结果的防重策略。无需重写评分、RSS、模型、Obsidian 或内容 Prompt。

主要残余风险：

- 微信连接器没有业务级 idempotency key，无法同时数学保证“不重复”和“绝不漏发”。本实现对未知结果选择隔离，优先避免重复，并保留人工复核证据。
- Cindy schedule 是外部运行配置；仓库更新不会自动替换旧 prompt 或绑定 session。必须通过 Cindy 支持的 scheduler 控制面做一次性部署，禁止私写数据库。
- URL 处理是异步的。即时回复只确认收件，不假装分析已经完成；最终结果由每两分钟检查一次、空闲时静默跳过的 bound requested schedule 发回。
- 微信文章可能受登录、验证页或正文访问限制。此时必须返回可理解的失败，不得用相似搜索结果冒充原文。

## 验证与日常检查

```bash
scripts/control.sh start-tmux
python -m pytest -q tests/test_wechat_outbox.py tests/test_wechat_queue.py tests/test_cindy_control.py
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
