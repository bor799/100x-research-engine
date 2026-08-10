# Information Tracking Agent

A multi-platform knowledge extraction agent that captures decision-grade signals from the information flood — scored by LLM, written to Obsidian, pushed to Telegram.

[中文文档](README.md)

---

## Why This Exists

RSS readers are everywhere. They all do the same thing: dump everything on you and let you sort through it.

The problem is you can't sort through it. 200 articles land, 190 are noise, and maybe 1 in the remaining 10 could change a decision. You spend more time filtering than reading the thing that matters.

So this isn't another RSS reader. It's a tool for fighting information asymmetry — you tell it what you care about, it reads the 200 for you, and hands you the 1.

The way it does this isn't through rule-matching. It's through a digital twin that, after reading hundreds of pieces, knows what you want to read next better than you do.

---

## Design Philosophy

This system went through three generations. The core difference between them isn't feature count — it's **who makes the decisions**.

### V1: The Diligent But Stupid Calculator

V1 was the product of traditional backend instincts — a deterministic pipeline, woken by Cron, blindly scanning 7 days of data, processing each item, pushing results.

Its biggest problem wasn't slowness. It was **silent bleeding**. When the LLM API went down, the system didn't error out — it "smartly" scored articles by word count. 200 articles all passed the initial filter with 7.0+ scores, then silently failed in the analysis stage. The system appeared to be running at full speed. Actual output: zero.

For AI systems, the scariest thing isn't a crash. It's failing when nobody notices.

### V2: The Pivot From Script to Agent

V2 had one core belief: **surrender micro-control**.

Traditional programmer instincts want to handle every edge case. But that's exactly what limits AI's strength — adaptive capability. So V2 did three things:

**Incremental perception.** The scheduler wakes up with memory — it knows what page it read last time and only fetches new items. The hardcoded `RSS_DAYS_LIMIT = 7` was eliminated entirely. With memory, it grabs increments. Without memory, it dynamically calculates the lookback window from the schedule interval.

**Cognitive resource layering.** The flagship model no longer does coarse filtering. The system mimics human "fast intuition" vs "deep thinking" — coarse screening uses lightweight models (the admin assistant), deep extraction uses the flagship model (the advisory board). This isn't optimization — it's the philosophy of compute governance.

**Fail Loudly.** The "score by word count when API fails" fallback was completely removed. When a model call fails, an alert fires immediately and the task enters the failure queue. The system would rather halt than let garbage pollute the knowledge base.

### V3: A Digital Organism That Evolves

V3 turns "evolution" itself into a system capability.

**Memory layering.** Short-term focus captures your dense concerns over the past 1-2 weeks, automatically boosting extraction weight for related signals. The core persona carries your stable values and mental models. Neither is static configuration — both evolve continuously through your reading behavior.

**From injection to evolution.** The system doesn't passively accept configuration. It infers your shifting interests from extraction history — articles you marked "worth re-reading", topics you repeatedly search — and proposes updates to your focus areas.

**Skill internalization.** This is the most fundamental architectural change. In V2, WeChat scraping worked like a crayfish — the claws were bolted on externally. Every time it needed to grab something, it had to leave the room, start up a harvester machine, wait for it to spit out a page, then pick up the paper and read it. V3 injects scraping capability directly into the process — no longer a "foreman directing robots", but a digital organism that grows new organs. When scraping fails, it "hurts". When it hits anti-bot walls, it "finds another route". When the toolchain breaks, it "self-diagnoses".

---

## How It Works

```
Sources (URL / RSS / IM)
  |
  v
QueueStore  --------  Task queue, SQLite-backed
  |
  v
RuntimeGuard  ------  Runtime isolation, rejects contaminated paths
  |
  v
Fetch  -------------  Multi-platform, 9+ channels, adaptive routing
  |
  v
Score  -------------  Four-layer scoring engine
  |                     L1 signal strength x L2 source credibility x L3 timeliness
  |                     = objective quality
  |                     final = 70% objective + 30% personal fit (L4)
  v
Extract  ------------  LLM deep extraction, structured knowledge cards
  |
  v
Output  -------------  Obsidian Markdown + Telegram push
```

Scoring contract:

```python
objective_quality = L1 * L2 * L3
final_score       = 0.70 * objective_quality + 0.30 * L4
score             = final_score * 10  # 0-10 scale
```

Every failure point produces a typed outcome — `retry_scheduled`, `rejected`, or `failed_terminal`. Nothing gets marked as done when it isn't.

---

## Supported Platforms

| Platform | Method | Notes |
|----------|--------|-------|
| YouTube | yt-dlp | Video metadata and subtitles |
| Twitter/X | xreach / MCP | Tweets and threads |
| Reddit | Jina Reader | Posts and comments |
| V2EX | Jina Reader | Discussions |
| Hacker News | Jina Reader | Tech threads |
| WeChat Official Accounts | Agent-Reach | Full article (built-in skill) |
| Xiaoyuzhou FM | Jina Reader | Podcast metadata |
| RSS/Atom | stdlib xml | Feed aggregation |
| Web | Jina Reader | Any URL |

**MCP extensibility:** The architecture supports plugging in external information source services via MCP protocol (e.g., Mindspace MCP) for search, channel management, and article retrieval — no core code changes needed.

**Web search:** Supports Exa API direct calls or MCP mode, with category filters, domain restrictions, and recency filtering.

---

## Quick Start

### Install

```bash
git clone https://github.com/bor799/information-tracking-agent.git
cd information-tracking-agent
pip install -e .
```

### Configure

```bash
cp config/config.example.yaml config/config.local.yaml
cp .env.example .env
```

Edit `.env` with your API key (at least one LLM provider required). See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for full reference.

### Run

```bash
# Enqueue a URL
information-tracking-agent enqueue-url "https://example.com/article"

# Process pending tasks (staging mode)
information-tracking-agent worker-once --limit 10

# Check queue status
scripts/control.sh status
```

---

## Project Structure

```
src/knowledge_extractor_v3/
  __init__.py
  shadow_runner.py       # CLI entry point
  pipeline.py            # Processing pipeline orchestration
  worker.py              # Queue consumer and batch processor
  scheduler.py           # Scheduled source polling
  queue_store.py         # SQLite task queue
  runtime_guard.py       # Runtime isolation and fingerprinting
  config_loader.py       # Layered config loading
  models.py              # Data models
  health.py              # Health monitoring
  prompt_registry.py     # Prompt version management
  prompt_parser.py       # LLM output parsing
  llm/                   # LLM provider abstraction
  fetchers/              # Multi-channel fetch adapters
  sources/               # Source registry and deduplication
  outputs/               # Obsidian + Telegram output
```

40 source files, 24 test files, 224+ test cases.

---

## Testing

```bash
pip install -e ".[dev]"
python -m pytest -q              # 224+ tests
python -m compileall src tests   # Compile check
```

CI runs on Python 3.11 / 3.12 / 3.13.

---

## Documentation

| Doc | Description |
|-----|-------------|
| [Configuration Guide](docs/CONFIGURATION.md) | Environment variables, YAML config, safe defaults |
| [Architecture](docs/architecture.md) | Data flow, queue contract, prompt contract |
| [Error Architecture](docs/error-architecture.md) | 9 historical error types and V3 structural defenses |
| [Search Capabilities](docs/SEARCH_CAPABILITIES.md) | Exa search, MCP mode |
| [RSS Architecture Analysis](docs/RSS_FETCH_ARCHITECTURE_ANALYSIS.md) | RSS fetch failure analysis and optimization |

---

## Command Reference

```bash
# Enqueue
information-tracking-agent enqueue-url <URL>

# Process
information-tracking-agent worker-once --limit N --mode live|staging

# System management
scripts/control.sh status    # View status
scripts/control.sh recover   # Recover resident services
```

---

## License

MIT
