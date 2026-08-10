# Configuration Guide / 配置指南

Information Tracking Agent uses a layered configuration system: YAML files for structure, environment variables for secrets.

## Configuration Layers

1. `config/config.example.yaml` — tracked defaults (safe to commit)
2. `config/config.local.yaml` — local overrides (gitignored, contains real keys)
3. `.env` — environment variables (gitignored, contains secrets)

Environment variables take precedence over YAML values.

---

## Environment Variables / 环境变量

### Core / 核心

| Variable | Default | Description |
|----------|---------|-------------|
| `STATE_ROOT` | `~/.100x_v3` | Root directory for all runtime state (queue DB, logs, roles) |
| `QUEUE_DB_PATH` | `~/.100x_v3/queue.db` | SQLite queue database path |
| `LOG_PATH` | `~/100x-v3-daemon.log` | Daemon log file path |
| `CONFIG_PATH` | `config/config.local.yaml` | Config file to load |

### LLM Provider / LLM 提供者

| Variable | Required | Description |
|----------|----------|-------------|
| `ZHIPU_API_KEY` | Yes* | Zhipu AI API key (primary provider) |
| `OPENAI_API_KEY` | No | OpenAI API key (fallback provider) |

\* Or whichever provider you configure as primary in `config.yaml`.

### Telegram Output / Telegram 输出

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token for notifications |
| `TELEGRAM_ADMIN_CHAT_ID` | No | Telegram chat ID for admin notifications |

Telegram output is optional. The system works without it — output goes to Obsidian only.

### Web Search / 网络搜索

| Variable | Required | Description |
|----------|----------|-------------|
| `EXA_API_KEY` | No | Exa API key for web search (direct API mode) |

If not set, search can still work via MCP mode (no API key needed).

### Multi-Platform Fetching / 多平台抓取

| Variable | Default | Description |
|----------|---------|-------------|
| `HTTP_PROXY` | — | HTTP proxy for web requests |
| `HTTPS_PROXY` | — | HTTPS proxy for web requests |
| `TWITTER_AUTH_TOKEN` | — | Twitter/X auth token (from browser localStorage) |
| `TWITTER_CT0` | — | Twitter/X CSRF token |
| `XREACH_PATH` | auto-detect | Path to xreach CLI binary |
| `YT_DLP_PATH` | auto-detect | Path to yt-dlp binary |

---

## YAML Configuration / YAML 配置

### `runtime` — Runtime Paths

```yaml
runtime:
  state_root: "~/.100x_v3"           # All state lives here
  queue_db_path: "~/.100x_v3/queue.db"
  log_path: "~/100x-v3-daemon.log"
```

### `live` — Live Mode Control

```yaml
live:
  enabled: false                      # Must be explicitly enabled
  require_runtime_guard: true         # Enforce path isolation
  require_operator_confirmation: false
  max_tasks_per_run: 0                # 0 = unlimited
  max_consecutive_failures: 5         # Pause after N consecutive failures
```

### `llm` — LLM Provider Settings

```yaml
llm:
  provider: "placeholder"             # Provider name
  api_key_env: "ZHIPU_API_KEY"        # Env var holding the API key
  api_base: "https://open.bigmodel.cn/api/coding/paas/v4"
  scoring_model: "placeholder-scoring-model"
  extraction_model: "placeholder-extraction-model"
  telegram_brief_model: "placeholder-brief-model"
  request_timeout_seconds: 60
  max_retries: 3
  temperature: 0.1
  fallback_providers:                 # Tried on 429/quota errors
    - provider: "openai"
      api_key_env: "OPENAI_API_KEY"
      scoring_model: "gpt-4o-mini"
      extraction_model: "gpt-4o-mini"
```

### `outputs` — Output Configuration

```yaml
outputs:
  obsidian_root: ""                   # Obsidian vault path (empty = disabled)
  obsidian_subdir: "inbox"            # Subdirectory within vault
  write_manifest: true                # Write processing manifest
  telegram_bot_token_env: "TELEGRAM_BOT_TOKEN"
  telegram_admin_chat_id_env: "TELEGRAM_ADMIN_CHAT_ID"
  telegram_enabled: true              # Enable Telegram output (requires token)
```

### `prompts` — Prompt Versioning

```yaml
prompts:
  registry: "prompts/registry.json"
  active_bundle: "v2_stable_cn"       # Which prompt bundle to use
  scoring: "prompts/primary_market_scoring.md"
  extraction: "prompts/primary_market_extraction.md"
  telegram_brief: "prompts/telegram_brief.md"
```

### `sources` — Content Sources

```yaml
sources:
  - id: my-rss-feed
    type: rss
    url: https://example.com/feed.xml
    schedule: "0 */6 * * *"           # Cron expression
    tags: [tech, news]
    priority: 10

  - id: web-search-ai
    type: search
    config:
      query: "artificial intelligence news"
      num_results: 10
      recency: oneWeek
    schedule: "0 9 * * *"
    tags: [ai]

  - id: manual-urls
    type: url_list
    urls:
      - https://example.com/article1
      - https://example.com/article2
```

### `scheduler` — Scheduler Settings

```yaml
scheduler:
  enabled: false
  interval_seconds: 300              # Poll interval in seconds
```

### `worker` — Worker Settings

```yaml
worker:
  batch_size: 10                     # Items per batch
  poll_interval_seconds: 30          # Queue poll interval
```

### `agent_reach` — Multi-Platform Fetcher

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
  fallback_to_jina: true             # Fall back to Jina Reader on failure
  proxy: ""
  wechat:
    headless_first: true
    interactive_on_blocked: true
    profile_dir: "~/.100x_v3/browser-profiles/wechat"
    verification_timeout_seconds: 300
```

---

## Safe Defaults / 安全默认值

The system is designed to be safe out of the box:

- **Live mode disabled** — Must explicitly set `live.enabled: true`
- **Runtime isolation** — State is isolated to `~/.100x_v3`, separate from any V2 data
- **No output without configuration** — Obsidian and Telegram outputs require explicit paths/tokens
- **Staging mode by default** — Worker runs in staging mode unless `--mode live` is specified
