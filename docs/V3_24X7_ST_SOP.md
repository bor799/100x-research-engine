# V3 7x24H ST/SOP

记录时间：2026-05-05 17:45 CST

## 当前状态

V3 已启动，当前由 `tmux` 托管：

```bash
tmux attach -t 100x_v3_24h
```

四个服务：

| 服务 | 作用 |
| --- | --- |
| `scheduler-loop` | 抓取 RSS/多渠道内容 |
| `worker-loop` | 处理队列与 LLM 分析 |
| `telegram-bot-loop` | Telegram 交互 |
| `health-monitor` | 健康检查 |

检查命令：

```bash
bash scripts/control.sh status
bash scripts/control.sh tmux-status
```

一键恢复：

```bash
bash scripts/control.sh recover
```

## 这次的关键结论

不要把 7x24H 建立在“人每天盯着”上。

正确方向是：

1. 任务进队列，失败可重试。
2. 服务有 heartbeat，异常可发现。
3. 进程退出后由 watchdog/托管器自动拉起。
4. 人只通过一句话触发检查、恢复、汇总。
5. iOS 端只负责看见状态、整理结果、发出意图。

## 当前限制

`LaunchAgent` 自启失败过一次，原因是项目在：

```bash
~/Documents/Obsidian Vault/...
```

macOS 隐私权限阻止 launchd 访问 Documents，日志表现为：

```text
Operation not permitted
exit code 126
```

所以当前先用 `tmux`。它能跑，但电脑重启、用户退出、深度睡眠后需要恢复。

## 风险处理

| 场景 | 处理 |
| --- | --- |
| 开机后没启动 | `bash scripts/control.sh recover` |
| 断网后没恢复 | 网络恢复后执行 `status`，异常再 `recover` |
| 合盖后暂停 | 开盖后执行 `status` |
| pending 堆积 | 看 `worker-loop.log`，必要时 `recover` |
| 想长期无人值守 | 迁到 VPS/NAS/Mac mini，或修复本机 LaunchAgent 权限 |

## 收敛路线

### 1. 现在

用 `tmux + recover` 保持可运行。

### 2. 下一步

增加 watchdog：每 1-5 分钟检查 heartbeat，服务死掉自动拉起。

### 3. 稳定版

迁移到常久在线机器，让本机和 iOS 只做控制台。

## 经验值

7x24H 的核心不是“永远不断”，而是“断了能回来、失败不丢、状态看得见”。

