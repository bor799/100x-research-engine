# V3 搜索能力

## 概述

V3 现在支持通过 Exa API 进行网络搜索，发现和获取内容。

## 两种模式

### 1. 直接 API 模式（推荐）

需要 Exa API key（从 https://exa.ai 获取）：

```bash
export EXA_API_KEY="your_api_key_here"
```

优点：
- 稳定可靠
- 支持所有功能
- 无需额外依赖

### 2. MCP 模式

使用 mcporter + Exa MCP 服务器：

```bash
# 安装 mcporter
pip install mcporter

# 配置 Exa MCP 服务器
mcporter config add exa https://mcp.exa.ai/mcp
```

优点：
- 无需 API key
- 适合开发和测试

## 使用方式

### 1. 作为 Fetcher（直接搜索获取内容）

```python
from knowledge_extractor_v3.fetchers import FetcherRouter

router = FetcherRouter()

# 搜索
results = router.search("AI industry news", num_results=5)

# 搜索并获取完整内容
fetched = router.search_and_fetch("Python async best practices", num_results=3)
for content in fetched:
    print(content.title)
    print(content.text[:200])
```

### 2. 作为 Source（定期搜索发现内容）

```python
from knowledge_extractor_v3.sources import SearchAdapter, create_search_source
from knowledge_extractor_v3.sources.registry import SourceRegistry

# 创建搜索源
search_source = create_search_source(
    id="ai-news",
    query="AI industry news 2024",
    num_results=10,
    recency="oneWeek",
    tags=["ai", "news"],
    priority=10,
)

# 注册到调度器
registry = SourceRegistry()
registry.register(SearchAdapter())
registry.add_source(search_source)

# 发现新内容
items = registry.discover_all(lookback_days=7)
```

## 支持的搜索参数

| 参数 | 类型 | 说明 |
|------|------|------|
| query | str | 搜索查询（自然语言推荐） |
| num_results | int | 结果数量（1-100） |
| category | str | 类别过滤（company, people 等） |
| domain | str | 限制到特定域名 |
| recency | str | 时间过滤（oneDay, oneWeek, oneMonth, oneYear, noLimit） |

## 支持的内容通道

现在支持以下平台的专门处理：

- **YouTube** - 视频信息（yt-dlp）
- **Twitter/X** - 推文内容（xreach）
- **Reddit** - 帖子和评论（Jina Reader）
- **V2EX** - 中文技术社区（Jina Reader）
- **Hacker News** - 技术新闻（Jina Reader）
- **微信公众号** - 文章内容（Jina Reader）
- **小宇宙** - 播客（Jina Reader）
- **RSS/Atom** - 订阅源（直接解析）
- **通用网页** - 任意 URL（Jina Reader）

## 配置示例

```yaml
# config/config.local.yaml
sources:
  - id: ai-search
    type: search
    config:
      query: "artificial intelligence news 2024"
      num_results: 10
      recency: oneWeek
    schedule: "0 */6 * * *"  # 每6小时
    tags: [ai, news]

  - id: reddit-ai
    type: rss
    url: https://www.reddit.com/r/artificial.rss
    schedule: "hourly"
    tags: [reddit, ai]

channels:
  enabled:
    - youtube
    - twitter
    - reddit
    - v2ex
    - hackernews
    - rss
    - web
```

## API 参考

### FetcherRouter.search()

```python
def search(
    query: str,
    *,
    num_results: int = 10,
    category: str | None = None,
    domain: str | None = None,
    recency: str | None = None,
) -> list[dict[str, Any]]:
    """执行网络搜索"""
```

### SearchAdapter.discover()

```python
def discover(
    source: SourceConfig,
    *,
    lookback_days: int = 7,
) -> list[SourceItem]:
    """通过搜索发现内容"""
```
