# 100X Research Engine

> **A production-oriented pipeline for turning noisy multi-source feeds into traceable knowledge.**

[中文文档](./README.md)

The problem is not a lack of information. It is the attention cost of filtering repeated, low-increment content. 100X organizes acquisition, scoring, extraction, queues, delivery, and runtime state into one durable research pipeline.

## What it does

```text
Multi-platform sources
        ↓
Fetch → Normalize → Deduplicate
        ↓
LLM Score → Deep Extraction → Route
        ↓
Durable Queue / Outbox
        ↓
Obsidian + Cindy / WeChat
        ↓
Run Log + Feedback
```

- **Reduce noise:** new material is evaluated for information gain before it reaches the reading queue.
- **Keep evidence:** tasks, retries, outcomes, and delivery receipts have durable state.
- **Reach multiple platforms:** RSS, Web, YouTube, Twitter/X, Reddit, V2EX, Hacker News, WeChat, and Xiaoyuzhou.
- **Operate safely:** control scripts handle preflight, start, stop, recovery, diagnostics, and logs.
- **Preserve human judgment:** AI fetches and extracts; a person decides what deserves long-term belief.

Runtime state is isolated under `~/.100x_v3`. `scripts/control.sh` is the production control surface; `scripts/run-v3.sh` exposes lower-level roles and preflight commands.

## Verified

Fresh local verification on 2026-08-24:

```text
332 passed in 3.40s
```

```bash
uv sync --python 3.13 --extra dev
uv run python -m compileall -q src tests scripts/v2_compare.py scripts/test_rss_fetch.py
uv run pytest -q
```

## Relationship to Reclaim Feed

- **100X** is the production research engine: acquisition, evaluation, queues, state, and reliable delivery.
- **[Reclaim Feed](https://github.com/bor799/reclaim-feed)** is the product-layer experiment: reading, annotation, and reuse.

They share one problem but evolve separately because operational reliability and product experience need different feedback loops.

## Repository map

```text
src/       Core package and runtime logic
tests/     Regression and contract tests
scripts/   Control plane, operations, and migrations
config/    Sources and runtime configuration
docs/      Architecture, errors, and runbooks
prompts/   Scoring, extraction, and output rules
```

## Boundaries

- It does not decide what a person should believe.
- Multiple similar sources do not automatically become independent evidence.
- External platform coverage depends on the corresponding tools and account environment.
- Final promotion into the knowledge system remains a human responsibility.

## My role

I defined the attention problem, information lifecycle, queue and delivery contracts, failure recovery, and the acceptance boundary for knowledge. AI handled much of the implementation, test expansion, and documentation work.
