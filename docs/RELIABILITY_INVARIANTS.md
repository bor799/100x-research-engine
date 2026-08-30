# 可靠性不变量（第一原理重设计，2026-08-30）

issue #7 审核暴露的四类 P0，本质上是对四条系统不变量的违反。本文是重设计的锚：
每条机制在写代码前先回答「它守护哪条不变量」，机制可以被替换，不变量不变。

## 四条不变量与机制映射

| # | 不变量 | 曾经的违反方式（机理） | 守护机制（本仓库） |
|---|---|---|---|
| I1 | **一个任务任一时刻至多被一个 worker 持有** | `next_ready_tasks` 是裸 SELECT，`mark_processing` 的 `WHERE id=?` 不看状态：多套 daemon 同时取到同一批任务、全部认领成功、全部跑 LLM、全部试图写档（P0-1 重复落档的队列层根因） | 认领变 CAS：`WHERE id=? AND status IN (pending, retry_scheduled)`，输家抛 `QueueClaimConflict` 并跳过（不算失败） |
| I2 | **只有持有者能改写终态；持有者干活时租约必须活着** | `update_heartbeat` 全库零调用——租约是认领时一次性写入。30 分钟回收器会把还在干活的长任务判死回收，造成"真·双处理"；更糟的是僵尸 worker 失败路径的 `schedule_retry` 能把已 DONE 的任务打回 retry_scheduled，再次处理 | worker 每次认领生成唯一 owner（hostname-pid-uuid，重启也不撞）；后台线程每 20s 心跳续租；终态迁移（done/rejected/retry）带 owner 守护，僵尸写被静默丢弃并留 stderr 证据 |
| I3 | **一个角色至多一个活跃进程；停止必须杀干净整棵进程树** | control.sh 的 stop 只 `kill` supervisor；supervisor 的 TERM 陷阱只杀直接子进程 `bash -lc`，bash 不转发信号 → python 孙进程被 init 收养成孤儿。三套栈并存 → 三倍处理 + 抢 8765 端口（P0-2）。daemon 又只接 KeyboardInterrupt，TERM 到了也当没看见 | daemon loop 模式对 queue.db 取 `flock` 单例锁，第二实例直接退出（锁不住时 CAS 仍兜底）；daemon 安装 SIGTERM 处理，批间轮询优雅退出；control.sh 改为进程组 TERM → 10s 宽限 → KILL 升级 |
| I4 | **推送必达或明确失败——迟到的消息宁可死信** | outbox 的 `expire()` 写好了却生产零调用：`claim()` 从不检查 `expires_at`，断连期间 pending 无限堆积（欠账），通道恢复后全部迟到倾泻到微信 | `claim()` 先清扫：过 TTL 的 pending 直接进 failed（`OUTBOX_EXPIRED`），永不投递；CLI 增加 `expire` 子命令供手动清算 |

8765 绑定失败也归 I3/I4 之间：孤儿占端口时，新 daemon 一次 bind 失败就把 magazine 服务
永久禁用（`magazine_server = False` 终身残废）。现在改为 300s 退避重试——可用性声明与
实际状态最终一致。

## 设计时序（为什么这些机制互为补位）

```
control.sh stop ──进程组kill──▶ 孤儿不可能存在 ──▶ 单端口单栈
       │
       └─仍泄漏（理论）─▶ daemon flock 单例锁 ─▶ 第二栈起不来
                                  │
                                  └─仍并发（锁跳过）─▶ 队列 CAS ─▶ 任务只被处理一次
                                          │
                                          └─长任务被误回收 ─▶ 心跳续租 ─▶ 只回收真死者的任务
                                                  │
                                                  └─僵尸醒来写终态 ─▶ owner 守护 ─▶ 赢家的状态不可被覆盖
```

每一层都假设上一层可能失效。vault 写入守卫（2026-08-30 上午）是最外层的最后一道闸：
即使以上全部失守，重复文件也不会落盘——但那是止损，不是设计。

## 与既有 dedup 层的关系

vault 四层去重（管道早退 / 同 URL 增量 / 写入守卫 / 周期清理）守护的是**落盘结果**
的幂等；本文件的四条不变量守护的是**处理过程**的幂等。过程幂等成立后，结果层的
守卫从"每天兜底的必需品"退化为"防御纵深"。

## 验证

`tests/test_queue_lease.py`（CAS/僵尸/心跳）、`tests/test_worker_lease.py`（认领竞争/
批间短路/信号归属）、`tests/test_daemon.py`（单例锁/TERM 退出/绑定重试）、
`tests/test_wechat_outbox.py`（claim 先清扫）。基线见 CLAUDE.md。
