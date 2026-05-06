# V2 → V3 完整迁移计划

> 生成时间：2026-04-28
> 目标：V3 完全替代 V2，V2 彻底废弃

---

## 一、V2 功能盘点

### 1.1 核心功能（必须迁移）

| 模块 | V2 位置 | 功能描述 | 优先级 |
|------|---------|----------|--------|
| **RSS 抓取** | `src/fetchers/rss.py` | 定时抓取 39 个 RSS 源 | P0 |
| **多渠道抓取** | `src/fetchers/agent_reach_fetcher.py` + `ar_channels/` | YouTube, Twitter, 微信, 小宇宙, 通用网页 | P0 |
| **质量过滤** | `src/quality_filter.py` | GLM-4.5 评分 >= 7.0 | P0 |
| **深度萃取** | `src/analyzer.py` | GLM-4.7 深度萃取 | P0 |
| **队列管理** | `src/app_queue.py` | URL 队列、状态追踪 | P0 |
| **Obsidian 输出** | `src/outputs/obsidian.py` | 萃取结果落 Obsidian | P0 |
| **Telegram Bot** | `src/outputs/telegram.py` | 手动提交 URL、接收通知 | P0 |
| **守护进程** | `src/main.py` | bot + queueworker + schedule + daemon | P0 |
| **定时任务** | crontab | Horizon News (05:10) | P0 |

### 1.2 辅助功能（可选迁移）

| 模块 | 描述 | 优先级 |
|------|------|--------|
| **Skills** | `src/skills/` - 各种分析技能（earnings_call, detector 等） | P2 |
| **错误日志** | `src/error_logbook.py` | P2 |

### 1.3 已知 Bug（不要迁移）

| ID | 问题描述 | 影响 |
|----|---------|------|
| O1 | daemon 停机无告警 | 高可用性风险 |
| O2 | 队列消费静默失败 | 数据丢失 |
| O3 | Twitter Cookie 无自动刷新 | 运营隐患 |
| O4-O6 | RSS 时间解析缺陷 | 部分文章丢失 |
| O7 | LLM 限流无熔断 | 批量失败 |
| O8 | 运行时版本漂移 | 影子队列 |
| O9 | LLM 超时无重试 | 单条失败 |

---

## 二、V3 架构设计

### 2.1 核心原则

```
✅ 保留：V2 的核心功能和数据流
❌ 丢弃：V2 的架构缺陷和技术债务
🔄 重构：用 V3 的配置化和模块化重新实现
```

### 2.2 V3 模块映射

| V2 模块 | V3 对应 | 改进点 |
|---------|---------|--------|
| `src/main.py` | `src/main.py` | 简化、配置化 |
| `src/fetchers/rss.py` | `src/fetchers/rss_fetcher.py` | 重写、支持 ISO 8601 |
| `src/fetchers/agent_reach_fetcher.py` | `src/fetchers/multi_channel.py` | 复用 V2 的 ar_channels |
| `src/quality_filter.py` | `src/processors/quality_scorer.py` | 重写、增加熔断 |
| `src/analyzer.py` | `src/processors/deep_extractor.py` | 重写、增加重试 |
| `src/app_queue.py` | `src/queue/queue_manager.py` | 重写、统一状态根 |
| `src/outputs/obsidian.py` | `src/outputs/obsidian_writer.py` | 重写 |
| `src/outputs/telegram.py` | `src/outputs/telegram_bot.py` | 重写、简化 |
| `src/scheduler.py` | `config/config.local.yaml` | 配置化 |
| `.env` | `.env` | 复用 |
| `crontab` | `scripts/nightly_job.sh` | 统一脚本 |

### 2.3 V3 新增能力

| 能力 | 描述 |
|------|------|
| **配置化调度** | 通过 `config.local.yaml` 配置所有定时任务 |
| **统一状态根** | 显式传递 `STATE_ROOT`，避免影子队列 |
| **LLM 熔断** | 429/1308 自动降级重试 |
| **超时重试** | timeout 自动重试，支持指数退避 |
| **Cookie 自检** | Twitter Cookie 定时检测、自动告警 |
| **健康检查** | `scripts/run.sh doctor` 系统诊断 |
| **单入口脚本** | `scripts/run.sh` 统一管理所有命令 |

---

## 三、迁移步骤

### Phase 1: 停止 V2（5 分钟）

```bash
# 1. 停止 V2 进程
cd ~/Documents/Obsidian\ Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v2
./run.sh stop

# 2. 禁用 V2 cron
crontab -e
# 删除或注释掉：
# 10 5 * * * ~/Library/Application\ Support/nanobot/cron/horizon_bash.sh

# 3. 验证停止
ps aux | grep -i "100x\|horizon" | grep -v grep
```

### Phase 2: V3 功能补全（30-60 分钟）

#### 2.1 RSS 抓取器（高优先级）
```python
# src/fetchers/rss_fetcher.py
# - 支持 ISO 8601 时间解析（修复 O4）
# - 正确的时间过滤逻辑（修复 O6）
# - 配置化 RSS 源（config.local.yaml）
```

#### 2.2 质量评分器（高优先级）
```python
# src/processors/quality_scorer.py
# - GLM-4.5 评分
# - 429/1308 熔断机制（修复 O7）
# - 评分失败自动重试
```

#### 2.3 深度萃取器（高优先级）
```python
# src/processors/deep_extractor.py
# - GLM-4.7 深度萃取
# - 120s 超时重试（修复 O9）
# - 指数退避策略
```

#### 2.4 队列管理器（高优先级）
```python
# src/queue/queue_manager.py
# - 统一状态根（修复 O8）
# - failed 任务自动退避重试（修复 O2）
# - SQLite 队列
```

#### 2.5 Telegram Bot（高优先级）
```python
# src/outputs/telegram_bot.py
# - 手动提交 URL
# - 萃取结果通知
# - 错误告警（O1 daemon 停机告警）
```

#### 2.6 Obsidian Writer（高优先级）
```python
# src/outputs/obsidian_writer.py
# - 萃取结果落 Obsidian
# - 按来源分类
```

#### 2.7 主入口（高优先级）
```python
# src/main.py
# - daemon 模式（bot + queueworker + schedule）
# - 单次模式（url / rss / add）
# - 健康检查
```

#### 2.8 Twitter Cookie 自检（中优先级）
```python
# scripts/check_twitter_cookie.sh
# - 定时执行 `xreach auth check`
# - 失败时 Telegram 告警（修复 O3）
```

### Phase 3: 配置定时任务（5 分钟）

```bash
# 1. 安装 V3 cron
cd ~/Documents/Obsidian\ Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3
./scripts/install_crontab.sh

# 2. 验证
crontab -l
# 应该看到：
# 30 5 * * * /Users/murphy/.../v3/scripts/nightly_job.sh
```

### Phase 4: 测试验证（15 分钟）

```bash
# 1. 系统诊断
cd ~/Documents/Obsidian\ Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3
./scripts/run.sh doctor

# 2. 测试单条 URL
./scripts/run.sh url "https://lilianweng.github.io/posts/2023-06-23-agent/"

# 3. 测试 RSS
./scripts/run.sh rss

# 4. 启动 daemon
./scripts/run.sh start

# 5. 查看日志
tail -f ~/100x-v3-daemon.log

# 6. 查看 Obsidian 输出
ls -la ~/Documents/Obsidian\ Vault/信息源/AI进展/
```

### Phase 5: 废弃 V2（可选）

```bash
# 重命名 V2 目录（标记为废弃）
cd ~/Documents/Obsidian\ Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor
mv v2 v2.deprecated.$(date +%Y%m%d)

# 或者保留用于参考
# 不删除，只是不再使用
```

---

## 四、迁移检查清单

### 4.1 功能完整性

- [ ] RSS 抓取（39 个源）
- [ ] YouTube 抓取
- [ ] Twitter/X 抓取
- [ ] 微信公众号抓取
- [ ] 小宇宙播客抓取
- [ ] 通用网页抓取（Jina Reader）
- [ ] 质量评分（GLM-4.5，>= 7.0）
- [ ] 深度萃取（GLM-4.7）
- [ ] Obsidian 输出
- [ ] Telegram Bot（手动提交 + 通知）
- [ ] 队列管理
- [ ] 定时任务（夜间 05:30）

### 4.2 Bug 修复

- [ ] O1: daemon 停机告警
- [ ] O2: 队列消费不再静默失败
- [ ] O3: Twitter Cookie 自检 + 告警
- [ ] O4: RSS 时间解析支持 ISO 8601
- [ ] O5: 网页抓取超时处理
- [ ] O6: RSS 时间过滤逻辑修复
- [ ] O7: LLM 限流熔断
- [ ] O8: 统一状态根
- [ ] O9: LLM 超时重试

### 4.3 运行时验证

- [ ] V2 进程已停止
- [ ] V2 cron 已禁用
- [ ] V3 .env 已配置
- [ ] V3 config 已配置
- [ ] V3 cron 已安装
- [ ] V3 daemon 运行正常
- [ ] 单条 URL 测试通过
- [ ] RSS 测试通过
- [ ] Obsidian 输出正常
- [ ] Telegram 通知正常

---

## 五、回滚计划

如果 V3 出现问题，可以快速回滚到 V2：

```bash
# 1. 停止 V3
cd ~/Documents/Obsidian\ Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3
./scripts/run.sh stop

# 2. 禁用 V3 cron
crontab -e
# 删除或注释掉 V3 的 cron 任务

# 3. 启动 V2
cd ~/Documents/Obsidian\ Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v2
./scripts/run.sh start

# 4. 启用 V2 cron
crontab -e
# 添加回：
# 10 5 * * * ~/Library/Application\ Support/nanobot/cron/horizon_bash.sh
```

---

## 六、后续优化（V3.1）

- [ ] 多渠道交互（微信）
- [ ] Agent 自愈能力
- [ ] 任务列表管理
- [ ] 工具箱配置化
- [ ] 上下文压缩
- [ ] 持久化记忆

---

## 七、总结

**V2 → V3 不是简单的代码迁移，而是架构升级：**

| 维度 | V2 | V3 |
|------|----|----|
| **架构** | 脚本/看门狗 | 配置化/模块化 |
| **状态管理** | 隐式（易漂移） | 显式（统一根） |
| **错误处理** | 静默失败 | 熔断+重试+告警 |
| **调度** | 硬编码 | 配置化 |
| **可维护性** | 低（影子队列、版本漂移） | 高（健康检查、诊断工具） |
| **可扩展性** | 低 | 高（配置驱动） |

**明天早上 05:30，你将会看到：**

✅ V3 夜间任务自动运行
✅ 39 个 RSS 源被抓取
✅ 多渠道内容被萃取
✅ 结果落 Obsidian
✅ Telegram 通知发送
✅ 日志清晰可追踪

---

**准备好了吗？让我知道何时开始执行！**
