# 100X Knowledge Extractor V4

V4 is the absorption-first rewrite of the 100X knowledge extraction system:
articles are input, one LLM call absorbs each one, and the score ranks content
into lanes instead of gate-keeping it.

## V4 Pipeline

```
scan (95+ RSS sources) -> fetch (multi-channel) -> absorb (1x GLM-5.2 call)
  -> code-weighted score (0.40 gain / 0.35 action / 0.25 relevance)
  -> route: spam or <4.0 drop | 4.0-7.4 archive | >=7.5 push
             (action_value >= 0.70 -> daily business lane, else weekly digest)
  -> deterministic card render -> Obsidian archive + WeChat outbox
```

- One prompt (`prompts/absorption.md`), one LLM call, code-owned scoring.
- No source-credibility scoring: sources are hand-curated and trusted.
- Quotes must appear verbatim in the source or the section is omitted — never
  a failed push (V3 withheld 45 good briefs this way).

## Features

### Production Control Surface

- `scripts/control.sh` starts, stops, recovers, diagnoses, and tails the V4 roles (`loop` + `health-monitor`).
- The `loop` role runs `python -m knowledge_extractor_v3.daemon`: source scanning and queue working in one process.
- `scripts/run-v3.sh` exposes lower-level preflight, enqueue, worker, and log commands.
- Runtime state is isolated under `~/.100x_v3` by default and guarded before live roles touch the queue.

### Cindy / WeChat Control Plane

- 100X produces durable events; Cindy owns the authenticated WeChat session and delivery tool.
- The production channel is WeChat via the Cindy schedule (daily business pushes 8:30/11:30/17:20, weekly strategic digest Sunday 17:20).
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

V4 baseline on 2026-08-17: `275 passed` (single-call absorption core).

## Documentation

- [Search Capabilities](docs/SEARCH_CAPABILITIES.md) - Using the search feature
- [Architecture](docs/architecture.md) - System architecture
- [Error Architecture](docs/error-architecture.md) - Error handling design
- [V3 Autorun Operations](docs/V3_AUTORUN_OPERATIONS.md) - Production control and validation
- [V3 Release Handoff](docs/MIGRATION_COMPLETE.md) - Current V3 publish and remaining gates
