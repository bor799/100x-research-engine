# 100X V3 改造计划（最终版）

> **用途**：直接交给 Codex / Claude Code 执行。
> 项目根目录：`knowledge-extractor/v3/`
> 日期：2026-08-10
>
> **2026-08-11 内容契约修正**：微信只替代 Telegram 的传输通道，
> 不替代已经验证的内容产品。活动 bundle 继续使用
> `prompts/telegram_brief.md`；下文 Brief 方案已按该决定更新。

---

## 改造目标

| # | 变更 | 核心原则 | 状态 |
|---|------|---------|------|
| ① | 信息源 +elsewhere.news | 优先探测 RSS，不造轮子 | ✅ 完成 |
| ② | 评分偏好：可转述的小商业故事 | "小而具体"优先于"大而抽象" | ⬜ 待做 |
| ③ | Brief 遣词风格：表达的本质 | 字少密度高，砍掉不影响判断的内容 | ⬜ 待做 |
| ④ | 分发渠道 → Cindy 微信 | V3 写队列 → Cindy 定时投递 | ⬜ 待做 |

---

## 变更 ① 信息源 — 已完成 ✅

elsewhere.news 有标准 RSS feed（`https://elsewhere.news/feed.xml`），在 HTML `<link>` 标签中有声明但早期探测遗漏。

**已交付**：
- `config/sources.yaml` + `config/config.local.yaml`：新增 `type: rss` 源
- `src/.../sources/web_discovery.py`：通用 web 发现 adapter（bonus，给真正无 RSS 的站用）
- `scripts/find-feed.sh`：快速 RSS 发现脚本（先跑它再决定是否写代码）
- 274 tests passed，V3 守护进程已加载（Sources loaded: 96）

---

## 变更 ② 评分偏好 — 可转述的小商业故事

### 背景

当前 `v2_stable_cn/scoring.md` 已有"商业化/变现实操"1.5× 权重，但不够聚焦。核心偏好：

> 关注小的商业故事——具体的人/公司/场景，有起因-冲突-结果，能被转述给别人听的经验。
> 小生意、一线经营细节优先于宏大叙事。

### 文件改动

#### 2a. 新建 prompt bundle `v3_business_stories`

目录 `prompts/versions/v3_business_stories/`，基于 `v2_stable_cn` 复制后修改：

**`scoring.md` — 加分项表格调整**：

新增（放在表格最前）：

| 评分项 | 权重 | 说明 |
|---|---:|---|
| 可转述商业故事 | 2.0 | 包含具体的人/公司/场景，有起因-冲突-结果，能被转述。小生意、实操、踩坑复盘优先 |
| 小生意/一线经营 | 1.8 | 具体到定价、获客、成本、利润、团队管理的经营细节 |

调整已有项：
- "宏观/加密资产/AI 基建"权重 1.5 → **1.0**
- "个人成长/重要人物一手访谈"权重 1.2 → **1.5**

**`scoring.md` — 一票否决新增**：
```markdown
- 内容只有宏大叙事和行业概述，没有具体的人、公司、数字或可复述的情节。
```

**`scoring.md` — `content_compression` 说明新增**：
```markdown
- compressed_signal 必须是一句可被转述的话，而非抽象判断。
  好：「X公司用3个人做了200万ARR，关键是砍掉所有非核心功能」
  坏：「该文章讨论了SaaS商业模式的发展趋势」
```

#### 2b. 修改 `prompts/registry.json`

```json
"v3_business_stories": {
  "label": "V3 Business Stories",
  "description": "偏好可转述的小商业故事，信息密度优先的遣词风格，沿用已验证的结构化分发格式。",
  "roles": {
    "scoring": "prompts/versions/v3_business_stories/scoring.md",
    "extraction": "prompts/versions/v3_business_stories/extraction.md",
    "telegram_brief": "prompts/telegram_brief.md"
  }
}
```

`active_bundle` 改为 `"v3_business_stories"`。

### 验证
```bash
scripts/run-v3.sh enqueue-url "<elsewhere 某篇文章>"
scripts/run-v3.sh worker-once --limit 1 --mode live
# 确认评分 JSON 中 rationale 体现"可转述商业故事"维度
```

---

## 变更 ③ Brief 遣词风格 — 表达的本质

### 原则（最高优先级，写入 prompt 头部）

> 不是写完整，而是留下最重要的东西。
> 精炼 = 删除即使拿掉也不影响中心意思的内容。
> 字少，密度高，有记忆点。

### 文件改动

#### 3a. 复用 `prompts/telegram_brief.md`

不再为微信建立第二套内容模板。微信与 Telegram 共用同一份结构化 Brief：

**头部新增风格总纲**：
```markdown
## 表达原则（最高优先级，先读这条再看模板）

1. 精炼 = 删除即使拿掉也不影响中心意思的内容。
2. 背景、过程、过渡、边缘论证 → 对核心判断贡献不大的，砍掉。
3. 每句话必须独立有信息量。删掉后理解不变的，不该留。
4. 最终留下的文字应该像一个被压缩过的判断：字少，密度高，有记忆点。
5. 宁可少说三句，不可多留一句废话。

反模式（必须避免）：
- ❌ 「这篇文章主要讨论了…」（描述性开头）
- ❌ 「随着AI的发展，越来越多的…」（背景铺垫）
- ❌ 「值得注意的是…」「需要指出的是…」（过渡废话）
- ✅ 「3个人，200万ARR，秘诀是砍功能。」（判断，可记忆，可转述）
```

**输出模板保持为**：
```text
🎯 <标题>
🏷 <分类或 Signal Tier>

💡 <一句话归纳，最多 80 字>

🗣 1. 经验萃取
▪️ <具体经验、方法论或踩坑>

📡 2. 信号萃取
▪️ <行业趋势、产品/商业信号或反常识洞察>

🧭 3. 信源与压缩
▪️ 信源: <source_type/source_tier，利益关系>
▪️ 压缩: <保留的核心事实与丢弃的噪声>

💬 4. 核心金句
"<原文中最值得留下的句子>"

🛠 5. 下一步
▪️ <具体行动或监控触发器>

🔗 阅读原文: <plain URL>
```

经验、信号、信源压缩和下一步是该内容产品的核心价值，不因传输通道
从 Telegram 改为微信而删除。表达仍保持高密度，字数目标恢复为
手机 20 秒内读完、300-500 字，最多 500 字（URL 不计入）。

#### 3b. 修改 `prompts/versions/v3_business_stories/extraction.md`

在 `obsidian_brief_markdown` 模板说明中新增遣词约束：
```markdown
## 遣词风格（适用于所有 markdown 输出）

遵循"表达的本质"原则：
- 每一句话都应该是压缩过的判断，不是描述。
- 保留具体的人名、公司名、数字、事件。删除"随着…的发展"、"值得注意的是"等过渡。
- 经验层只留可转述的故事：谁、做了什么、结果如何、学到了什么。
- 金句只留原文中最尖锐的一句，不要凑数。
```

### 验证
同一篇文章分别跑新旧 prompt，对比输出字数和信息密度。

---

## 变更 ④ 分发渠道 → Cindy 微信

### 架构

V3 是 Python 守护进程，不能直接调 Cindy MCP。采用队列桥接：

```
V3 Worker → 格式化 brief → 写 JSON 到 ~/.100x_v3/wechat_queue/
                                        ↓
                    Cindy 定时任务（cindy_scheduler，每 2h）
                                        ↓
                    读队列 → mcp__cindy_wechat 发送 → 删除已发送文件
```

### 文件改动

#### 4a. 新建 `src/knowledge_extractor_v3/outputs/wechat_queue.py`

接口与 `telegram_live.py` 一致（`deliver(content, text) -> tuple[str, str]`）：
- 写入 `~/.100x_v3/wechat_queue/{timestamp}_{slug}.json`
- JSON 内容：`{"text": "...", "url": "...", "score": 8.2, "created_at": "..."}`
- 返回 `("queued", preview)`

#### 4b. 修改 `src/knowledge_extractor_v3/pipeline.py`

输出路由：`outputs.channel = "wechat"` 时调用 `wechat_queue.deliver()`。

#### 4c. 修改 `config/config.local.yaml`

```yaml
outputs:
  obsidian_root: "~/Documents/Obsidian Vault/信息源"
  obsidian_subdir: "AI进展"
  write_manifest: true
  channel: "wechat"              # wechat | telegram | both
  wechat_queue_dir: "~/.100x_v3/wechat_queue"
  telegram_enabled: false        # 保留但禁用
```

#### 4d. Cindy 定时投递任务

用 `cindy_scheduler` 创建每 2h 任务：
1. 读取 `~/.100x_v3/wechat_queue/*.json`
2. 每条调 `mcp__cindy_wechat__call_tool` 发送
3. 成功后删除文件，失败保留下次重试

### 验证
```bash
scripts/run-v3.sh worker-once --limit 1 --mode live
ls ~/.100x_v3/wechat_queue/   # 应有 JSON 文件
```

---

## 执行顺序

```
②③ Prompt 改造（纯 markdown，零风险）
     ↓
①   已完成
     ↓
④   微信分发（Python + Cindy 配置）
```

②③ 可以合并为一次提交——它们共享同一个新 bundle `v3_business_stories`。

### 全局验证
```bash
cd knowledge-extractor/v3
python -m compileall -q src tests
python -m pytest -q
scripts/run-v3.sh enqueue-url "https://elsewhere.news/zh/elsewhere/agi"
scripts/run-v3.sh worker-once --limit 1 --mode live
# 确认：评分体现商业故事偏好、Brief 保留结构化栏目且不超过 500 字、投递队列有文件
```
