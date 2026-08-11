# 100X Knowledge Extractor V3

V3 is a clean sibling repository for the primary-market version of the 100X knowledge extraction system.

## Features

### Production Control Surface

- `scripts/control.sh` starts, stops, recovers, diagnoses, and tails the V3 roles.
- `scripts/run-v3.sh` exposes lower-level preflight, enqueue, scheduler, worker, live role, and log commands.
- Runtime state is isolated under `~/.100x_v3` by default and guarded before live roles touch the queue.

### Cindy / WeChat Control Plane

- 100X produces durable events; Cindy owns the authenticated WeChat session and delivery tool.
- The default production channel is WeChat. Telegram remains an explicit rollback option and is not started by the production controller when disabled.
- Outbox delivery is all-state idempotent, capped at three attempts, and records sanitized receipts without raw peer/session/token values.
- Cindy-origin URL and status commands use the deterministic `scripts/cindy_control.py` interface.
- See [Cindy WeChat Operations](docs/CINDY_WECHAT_OPERATIONS.md) for the schedule contract and zero-touch runbook.

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
- **RSS Feeds**: `config/sources.yaml` is auto-loaded by `ConfigLoader` and currently carries 95 RSS sources after dedupe
- **Web Search**: Query-based content discovery

### Daily Reports

- US AI market daily report runner: `100x-v3-us-ai-daily`
- Python entrypoint: `python -m knowledge_extractor_v3.daily_reports.runner`
- Report output is routed by trading week and category, with system ledgers under the configured daily-report system directory.

## Safety Defaults

- Default state root: `~/.100x_v3`
- Default queue database: `~/.100x_v3/queue.db`
- Default log path: `~/100x-v3-daemon.log`
- Package name: `knowledge_extractor_v3`
- Python: `>=3.11,<3.14`

## Development

```bash
python -m compileall -q src tests scripts/v2_compare.py scripts/test_rss_fetch.py
bash -n scripts/*.sh
python -m pytest -q
```

Latest local verification on 2026-05-30: `259 passed`.

## Documentation

- [Search Capabilities](docs/SEARCH_CAPABILITIES.md) - Using the search feature
- [Architecture](docs/architecture.md) - System architecture
- [Error Architecture](docs/error-architecture.md) - Error handling design
- [V3 Autorun Operations](docs/V3_AUTORUN_OPERATIONS.md) - Production control and validation
- [V3 Release Handoff](docs/MIGRATION_COMPLETE.md) - Current V3 publish and remaining gates
