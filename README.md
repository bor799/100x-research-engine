# 100X Knowledge Extractor V3

V3 is a clean sibling repository for the primary-market version of the 100X knowledge extraction system.

## Features

### Multi-Channel Content Fetching

V3 supports fetching content from multiple platforms:

| Platform | Channel | Tool |
|----------|---------|------|
| YouTube | Video metadata | yt-dlp |
| Twitter/X | Tweets | xreach |
| Reddit | Posts/Comments | Jina Reader |
| V2EX | Discussions | Jina Reader |
| Hacker News | Threads | Jina Reader |
| 微信公众号 | Articles | Jina Reader |
| 小宇宙 FM | Podcasts | Jina Reader |
| RSS/Atom | Feeds | stdlib xml |
| Web | Any URL | Jina Reader |

### Web Search (Exa API)

- **Direct API mode**: Use Exa API key
- **MCP mode**: Use mcporter + Exa MCP server
- Supports category filters, domain restrictions, recency filters

### Source Discovery

- **URL List**: Manual URL input
- **RSS Feeds**: Periodic feed polling
- **Web Search**: Query-based content discovery

## Safety Defaults

- Default state root: `~/.100x_v3`
- Default queue database: `~/.100x_v3/queue.db`
- Default log path: `~/100x-v3-daemon.log`
- Package name: `knowledge_extractor_v3`
- Python: `>=3.11,<3.14`

## Development

```bash
python -m pytest
python -m compileall src tests
```

## Documentation

- [Search Capabilities](docs/SEARCH_CAPABILITIES.md) - Using the search feature
- [Architecture](docs/architecture.md) - System architecture
- [Error Architecture](docs/error-architecture.md) - Error handling design
