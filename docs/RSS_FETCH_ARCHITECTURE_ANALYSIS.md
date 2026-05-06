# RSS 抓取架构分析

> 分析时间：2026-04-29
> 触发原因：39 个 RSS 源中 17 个失败（44%），主要返回 HTTP 403

---

## 1. 问题根源分析

### 1.1 当前架构

V3 的 `RSSAdapter`（`src/knowledge_extractor_v3/sources/rss.py`）使用 Python 标准库 `urllib.request.urlopen()`：

```python
# 第 196 行
with urllib.request.urlopen(source.url, timeout=self._timeout) as response:
    feed_content = response.read().decode("utf-8", errors="replace")
```

**问题**：
- 没有设置任何 HTTP 请求头
- 没有 User-Agent
- 没有 Accept 头
- 无法处理需要浏览器特征的网站

### 1.2 失败的 RSS 源

| 源名称 | 错误 | 原因 |
|--------|------|------|
| Gary Marcus | HTTP 403 | 没有 User-Agent |
| Gwern | HTTP 403 | 没有 User-Agent |
| Jeff Geerling | HTTP 403 | 没有 User-Agent |
| lcamtuf | HTTP 403 | 没有 User-Agent |
| Overreacted | HTTP 403 | 没有 User-Agent |
| John D Cook | HTTP 403 | 没有 User-Agent |
| ...（共 16 个） | HTTP 403 | 没有 User-Agent |
| The Batch | HTTP 404 | RSS 地址已失效 |

### 1.3 验证测试

使用带 User-Agent 的请求测试：

```bash
# 测试 Gary Marcus RSS
curl -A "Mozilla/5.0" https://gabrielgabrie.github.io/feed.xml
# ✅ 成功返回 20 篇文章
```

**结论**：添加 User-Agent 可以解决大部分 403 问题。

---

## 2. 为什么不只用 User-Agent 补丁？

用户提出了正确的问题：**为什么不直接加一个 User-Agent？**

### 2.1 补丁式方案的问题

| 问题 | 说明 |
|------|------|
| **局部优化** | 只解决 RSS，WebPageFetcher、其他 source adapter 仍有问题 |
| **重复代码** | 每个地方都要设置 User-Agent、代理、超时 |
| **难以维护** | 以后要改重试逻辑、要加代理检测，要改多处 |
| **不一致** | RSS 用一种方式，Web 用另一种方式，Agent Reach 用第三种 |

### 2.2 V3 当前的不一致性

```python
# RSSAdapter - 用 urllib，无 User-Agent
urllib.request.urlopen(source.url)

# WebPageFetcher - 用 urllib，有 User-Agent
Request(url, headers={"User-Agent": self.user_agent})

# AgentReachFetcher - 用 requests，支持代理
requests.get(f"{self.JINA_READER_URL}{normalized_url}", proxies=proxies)
```

三种不同的 HTTP 客户端方式，没有统一的配置和错误处理。

---

## 3. Agent Reach 架构优势

### 3.1 Agent Reach 是什么？

Agent Reach 是 V2 的多渠道内容抓取系统，位于 `v2/src/fetchers/ar_channels/`。

**核心设计**：
1. **统一的 Channel 接口** - 每个平台一个 Channel Adapter
2. **智能路由** - 根据 URL 自动选择合适的 Channel
3. **多层 Fallback** - Channel 失败时自动降级
4. **代理支持** - 支持多种代理模式
5. **健康检查** - 每个都有 check() 方法

### 3.2 Channel 架构

```
BaseChannelAdapter (抽象基类)
├── can_handle(url)       # 判断能否处理该 URL
├── fetch(url, config)    # 获取内容
└── check(config)         # 健康检查
```

### 3.3 已实现的 Channel

| Channel | 平台 | 工具 | 能力 |
|---------|------|------|------|
| **YouTubeChannelAdapter** | YouTube | yt-dlp | 字幕提取、视频描述 |
| **TwitterChannelAdapter** | Twitter/X | xreach CLI | 推文、线程、链接展开 |
| **WechatChannelAdapter** | 微信公众号 | wechat-article-for-ai | 进程内调用、subprocess 降级 |
| **XiaoyuzhouChannelAdapter** | 小宇宙播客 | groq-whisper | 播客转录 |
| **WebChannelAdapter** | 通用网页 | Jina Reader | 绕过 403、arXiv 规范化 |

### 3.4 WebChannel 的关键能力

**Jina Reader Fallback**：
```python
JINA_READER_URL = "https://r.jina.ai/"
# 将任意 URL 转换为可读的 Markdown
# 自动绕过很多 403 限制
```

**示例**：
```bash
# 直接访问 Gary Marcus RSS
curl https://garymarcus.substack.com/feed
# ❌ HTTP 403

# 通过 Jina Reader
curl https://r.jina.ai/http://garymarcus.substack.com/feed
# ✅ 返回内容
```

### 3.5 Agent Reach 在 V3 的现状

V3 已有 `AgentReachFetcher`（`src/knowledge_extractor_v3/fetchers/multi_channel.py`）：

```python
class AgentReachFetcher:
    """基于 Agent-Reach 的统一内容获取器（V3 版本）

    支持的平台:
    - YouTube (yt-dlp)
    - Twitter/X (xreach CLI)
    - 微信公众号 (wechat-article-for-ai)
    - 小宇宙播客 (groq-whisper)
    - 通用网页 (Jina Reader fallback)
    """
```

**但当前问题**：
1. 通过 `sys.path` 引用 V2 的 ar_channels
2. 没有被 Scheduler/Worker 使用
3. RSS 没有走这个路由

---

## 4. 系统性架构升级方案

### 4.1 目标

1. **统一 HTTP 客户端层** - 所有抓取共用一个底层
2. **Agent Reach 为首选** - RSS、Web、特殊平台都走 Agent Reach
3. **渐进式迁移** - 先 RSS，后其他

### 4.2 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      Scheduler / Worker                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                   Fetcher Router (新增)                      │
│  根据 URL/domain/source_type 选择抓取方式                     │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
    ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
    │ Agent Reach  │  │ RSS Direct   │  │ Fixture      │
    │   (首选)     │  │   (降级)     │  │   (测试)     │
    └──────────────┘  └──────────────┘  └──────────────┘
              │
              ▼
    ┌───────────────────────────────────────────┐
    │         HTTP Client Layer (新增)           │
    │  - User-Agent                             │
    │  - 代理支持                                │
    │  - 重试逻辑                                │
    │  - 超时控制                                │
    │  - Jina Reader fallback                   │
    └───────────────────────────────────────────┘
```

### 4.3 实施步骤

#### Phase 1：统一 HTTP 客户端

创建 `src/knowledge_extractor_v3/fetchers/http_client.py`：

```python
class HttpClient:
    """统一的 HTTP 客户端"""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 30,
        max_retries: int = 3,
        proxy: str | None = None,
    ):
        ...

    def get(self, url: str) -> HttpClientResponse | TypedError:
        """发送 GET 请求，自动处理 User-Agent、代理、重试"""

    def get_via_jina(self, url: str) -> HttpClientResponse | TypedError:
        """通过 Jina Reader 获取（绕过 403）"""
```

#### Phase 2：改造 RSSAdapter

```python
class RSSAdapter:
    def __init__(self, http_client: HttpClient | None = None):
        self._http_client = http_client or HttpClient()

    def discover(self, source: SourceConfig, *, lookback_days: int):
        # 使用统一的 HTTP 客户端
        response = self._http_client.get(source.url)
        if isinstance(response, TypedError):
            # 降级到 Jina Reader
            response = self._http_client.get_via_jina(source.url)
        ...
```

#### Phase 3：Fetcher Router

```python
class FetcherRouter:
    """根据 URL 自动选择最佳 Fetcher"""

    def __init__(self):
        self.agent_reach = AgentReachFetcher()
        self.web = WebPageFetcher()
        self.fixture = FixtureFetcher()

    def route(self, url: str) -> FetchedContent | TypedError:
        # 1. 特殊平台走 Agent Reach
        if self._is_special_platform(url):
            return self.agent_reach.fetch(url)

        # 2. RSS feed URL 走 RSSAdapter（内部也用 HttpClient）
        if self._is_rss_feed(url):
            return self.rss_adapter.discover(...)

        # 3. 普通网页走 WebPageFetcher（或 Agent Reach WebChannel）
        return self.web.fetch(url)
```

#### Phase 4：V3 Native Agent Reach

将 V2 的 ar_channels 迁移到 V3，不再依赖 `sys.path`：

```
v3/src/knowledge_extractor_v3/channels/
├── __init__.py
├── base.py          # BaseChannelAdapter
├── youtube.py       # YouTubeChannelAdapter
├── twitter.py       # TwitterChannelAdapter
├── wechat.py        # WechatChannelAdapter
├── xiaoyuzhou.py    # XiaoyuzhouChannelAdapter
└── web.py           # WebChannelAdapter (Jina Reader)
```

### 4.4 预期效果

| 指标 | 当前 | 目标 |
|------|------|------|
| RSS 成功率 | 56% (22/39) | 95%+ |
| 统一 HTTP 客户端 | ❌ | ✅ |
| Agent Reach 集成 | 部分 | 完整 |
| 代码重复 | 高 | 低 |
| 维护性 | 低 | 高 |

---

## 5. 与迁移文档的关联

这个问题需要在 `MIGRATION_COMPLETE.md` 中添加到：

### Section 2.3 新增：RSS 抓取问题

- 当前 RSS 源 44% 失败率
- 原因：缺少 User-Agent
- 短期：添加 HttpClient 层
- 长期：全面使用 Agent Reach

### Section 4 P0 任务更新

- **RSS adapter** 优先级提升
- 增加统一 HTTP 客户端任务
- Agent Reach native 实现任务

### Section 5 Phase 2 更新

- 修复 RSS 抓取问题
- 实现 HttpClient
- 验证 RSS 成功率 > 95%

---

## 6. 关键设计决策

### 为什么选择 Agent Reach 而不是只用 User-Agent？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **加 User-Agent** | 简单快速 | 局部优化、代码重复 |
| **统一 HttpClient** | 解决一致性问题 | 不支持特殊平台 |
| **Agent Reach** | 统一、多平台、Jina fallback | 需要迁移 V2 代码 |

**决策**：渐进式实施
1. 先统一 HttpClient（解决 RSS 403）
2. 再整合 Agent Reach（统一所有抓取）
3. 最后 V3 native 实现（移除 sys.path 依赖）

---

## 7. 下一步行动

1. ✅ 架构分析完成
2. ⏳ 创建 HttpClient 实现
3. ⏳ 改造 RSSAdapter
4. ⏳ 更新迁移文档
5. ⏳ 实现 Fetcher Router
6. ⏳ V3 native Agent Reach
