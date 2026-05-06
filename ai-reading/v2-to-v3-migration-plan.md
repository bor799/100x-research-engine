# V2 → V3 版本迁移计划

## 概述

将 V2 的所有运行时配置迁移到 V3，实现：
- 同一 Telegram Bot Token 切换到 V3
- 70+ RSS 信息源迁移
- Agent Reach 多渠道抓取能力迁移
- Prompt 版本化管理（V2 prompts 作为 v2_legacy baseline）
- V2 归档备份，V3 作为生产版本

---

## 一、配置迁移清单

### 1.1 Bot 配置

| 项目 | V2 值 | V3 配置 |
|------|-------|---------|
| Telegram Token | `8216755400:AAH0nx_gOwZUAKmQ--rlPa5uGRMG5Jicnbk` | `TELEGRAM_BOT_TOKEN` 环境变量 |
| Chat ID | `7934670950` | `TELEGRAM_ADMIN_CHAT_ID` 环境变量 |

**操作**: 在 V3 项目根目录创建 `.env` 文件

```bash
TELEGRAM_BOT_TOKEN=8216755400:AAH0nx_gOwZUAKmQ--rlPa5uGRMG5Jicnbk
TELEGRAM_ADMIN_CHAT_ID=7934670950
ZHIPU_API_KEY=<从环境或V2配置获取>
```

### 1.2 LLM 配置

| V2 路由 | V3 配置项 | 值 |
|---------|-----------|-----|
| `llm.router.quality_filter.model` | `llm.scoring_model` | `GLM-4.5` |
| `llm.router.deep_analysis.model` | `llm.extraction_model` | `GLM-4.7` |
| `llm.router.telegram_format.model` | `llm.telegram_brief_model` | `GLM-4.5-Air` |
| `llm.provider` | `llm.provider` | `zhipu` |
| `llm.api_base` | (需新增) | `https://open.bigmodel.cn/api/coding/paas/v4` |

### 1.3 输出配置

| V2 | V3 |
|----|----|
| `output.obsidian_root: ~/Documents/Obsidian Vault/信息源` | `outputs.obsidian_root` |
| `output.obsidian_folder: AI进展` | `outputs.obsidian_subdir` |

### 1.4 信息源 (70+ RSS)

需要格式转换：

```yaml
# V2 格式
- name: HackerNews
  type: rss
  url: https://hnrss.org/frontpage
  cron_interval: 12h
  category: 技术资讯
  enabled: true

# V3 格式
- name: HackerNews
  type: rss
  url: https://hnrss.org/frontpage
  enabled: true
```

**注意**: V3 的 `cron_interval` 改为全局 `scheduler.interval_seconds`

---

## 二、Agent Reach 迁移

### 2.1 V2 Agent Reach 架构

```
AgentReachFetcher
├── YouTubeChannelAdapter (yt-dlp)
├── TwitterChannelAdapter (xreach CLI)
├── WechatChannelAdapter (wechat-article-for-ai)
├── XiaoyuzhouChannelAdapter (groq-whisper)
└── WebChannelAdapter (Jina Reader fallback)
```

### 2.2 V3 移植方案

**方案 C（推荐）**: 在 V3 新增 `fetchers/multi_channel.py`

```
src/knowledge_extractor_v3/fetchers/
├── base.py (Fetcher Protocol)
├── web.py (现有 WebPageFetcher)
├── fixture.py
└── multi_channel.py (新增)
```

`multi_channel.py` 内部复用 V2 的 Agent Reach 逻辑，但输出符合 V3 的 `FetchedContent` 格式。

### 2.3 配置扩展

在 `config.example.yaml` 新增 `agent_reach` section：

```yaml
agent_reach:
  enabled: true
  config_path: "~/.agent-reach/config.yaml"
  enabled_channels:
    - youtube
    - twitter
    - wechat
    - xiaoyuzhou
    - web
  fallback_to_jina: true
  proxy: ""
```

---

## 三、Prompt 版本管理

### 3.1 现有状态

V2 prompts 已经作为 `v2_legacy` bundle 放入 `prompts/registry.json`：

```json
{
  "active_bundle": "primary_market_v1",
  "parallel_test_bundles": ["primary_market_v1", "v2_legacy"],
  "bundles": {
    "v2_legacy": {
      "label": "V2 Legacy Baseline",
      "description": "Unmodified V2 screening and extraction prompts for baseline comparison only.",
      "roles": {
        "scoring": "prompts/versions/v2_legacy/scoring.md",
        "extraction": "prompts/versions/v2_legacy/extraction.md",
        "telegram_brief": "prompts/telegram_brief.md"
      }
    }
  }
}
```

### 3.2 切换机制

运行时可通过修改 `active_bundle` 切换版本：

```python
# 切换到 V2 prompts
config.prompts.active_bundle = "v2_legacy"

# 切换回 V3 prompts
config.prompts.active_bundle = "primary_market_v1"
```

---

## 四、切换步骤

### 阶段 1: 准备工作

- [ ] 停止 V2 运行
- [ ] 备份 V2 配置 (`config/config.yaml`)
- [ ] 备份 V2 state (如有重要队列数据)

### 阶段 2: 核心迁移

- [ ] 创建 V3 `.env` 文件，写入 Bot Token
- [ ] 移植 Agent Reach 到 V3 (`fetchers/multi_channel.py`)
- [ ] 转换 70+ RSS 源配置到 V3 格式
- [ ] 更新 V3 `config.local.yaml` (outputs, llm, scheduler)

### 阶段 3: 验证测试

- [ ] 启动 V3 telegram_bot 接收测试
- [ ] 发送各渠道测试链接验证抓取
  - YouTube: `https://www.youtube.com/watch?v=...`
  - Twitter: `https://x.com/...`
  - 微信: `https://mp.weixin.qq.com/...`
  - 小宇宙: `https://xiaoyuzhoufm.com/...`
  - 通用网页: 任意博客文章
- [ ] 验证 Obsidian 输出格式正确
- [ ] 验证 Prompt 切换功能

### 阶段 4: 切换完成

- [ ] V3 设为生产运行
- [ ] V2 目录归档（重命名为 `v2.deprecated` 或保留备份）

---

## 五、回滚方案

如果 V3 出现问题：

1. **Bot 回滚**: 停止 V3 bot，启动 V2 bot
2. **配置回滚**: 使用备份的 V2 `config.yaml`
3. **数据回滚**: V2 队列数据不受影响（独立存储）

---

## 六、注意事项

1. **Token 唯一性**: 同一 token 不能同时被 V2 和 V3 使用
2. **State 隔离**: V2 和 V3 使用不同的 state 目录
   - V2: 无专门 state (或使用项目内 queue.db)
   - V3: `~/.100x_v3/`
3. **环境变量优先**: 敏感信息优先用环境变量管理
4. **迁移验证**: 迁移后先 dry-run 测试，确认输出格式正确

---

## 七、后续优化

- [ ] 实现 Prompt 热切换（无需重启）
- [ ] 实现配置热加载
- [ ] 添加迁移后的健康检查脚本
- [ ] 文档化 V3 运维手册
