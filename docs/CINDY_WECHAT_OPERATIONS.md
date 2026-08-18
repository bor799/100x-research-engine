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

## 生产契约

- 默认生产输出为 `wechat`；Telegram delivery 与 Telegram bot 均关闭，但代码保留以便显式回滚。
- Cindy 生产 task 必须绑定一个已验证的 WeChat-origin session。禁止依赖“最近联系人”。
- Cindy 只发送 claim 返回的 `text`，不重写内容。
- `event_id` 由永久 idempotency marker 去重；即使 sent ledger 被清理，同一事件也不能重放。
- claim 开始一次 attempt；ack/nack 写入时间线和脱敏 receipt。第三次失败进入 `failed/`。
- recipient/session 只保留 SHA-256 引用。Connector 不提供某字段时保持空值并写入 `observability_gaps`，不猜测。
- 不直接修改 Cindy 数据库，不把 raw peer/session/context token 写入仓库或 prompt。

## Cindy 任务

生产保留两个既有节奏，共用同一个消费者协议（cron 由 Cindy schedule 持有，以实际配置为准）：

| Task | 节奏 | Lane | 最大条数 | Session |
|---|---|---|---:|---|
| 100X 商业故事推送 | 每天 8:30 / 11:30 / 17:20 | business | 2 | 同一个 verified bound session |
| 100X 战略观察周报 | 每周日 17:20 | strategic | 2 | 同一个 verified bound session |

两个 task 使用 [固定 delivery prompt](../prompts/cindy_wechat_delivery.md)，并分别使用：

- `scripts/schedule-checks/100x.mjs`
- `scripts/schedule-checks/100x-2.mjs`

Pre-run hook 在 Outbox 为空时以 code 2 静默跳过，避免无意义地启动 agent。

## 微信交互入口

用户在已绑定的 Cindy 微信会话中发 URL 或询问状态时，Cindy 只需调用确定性 CLI：

```bash
python scripts/cindy_control.py enqueue-url "https://example.com/article"
python scripts/cindy_control.py status
python scripts/cindy_control.py status --task-id 123
```

CLI 输出 JSON，Cindy 可以原样压缩成微信回复。它不接触 connector 身份，也不需要 Telegram。

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
