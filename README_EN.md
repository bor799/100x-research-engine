# 100X Research Engine

> **A production-oriented pipeline for turning noisy multi-source feeds into traceable knowledge.**

[中文文档](./README.md) · [![last commit](https://img.shields.io/github/last-commit/bor799/100x-research-engine)](https://github.com/bor799/100x-research-engine/commits/main) · [![Magazine](https://img.shields.io/badge/Magazine-GitHub_Pages-a63228)](https://bor799.github.io/100x-research-engine/)

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

<!-- RESULTS:BEGIN -->
## Results

This pipeline runs daily in production, not as an experiment:

| Output | Cadence | Archive |
| --- | --- | --- |
| Daily Wire | Weekdays | 42 issues |
| Weekly Digest | Weekly (Tue) | 9 issues |
| Deep Read | Occasional | 3 issues |

54 issues readable online, auto-published every Tuesday 19:00 (Asia/Shanghai) · ![last commit](https://img.shields.io/github/last-commit/bor799/100x-research-engine) · Screenshots in the [Appendix](#appendix--live-demo)
<!-- RESULTS:END -->

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

<!-- DEMO:BEGIN -->
## Appendix · Live Demo

A fully automated personal knowledge magazine, published to GitHub Pages:

[![Magazine home](项目效果展示/screenshots/index.png)](https://bor799.github.io/100x-research-engine/)

| Lane | Cadence | Latest issue |
| --- | --- | --- |
| Daily Wire | Weekdays | [每日短讯-2026-10-27](https://bor799.github.io/100x-research-engine/%E6%AF%8F%E6%97%A5%E7%9F%AD%E8%AE%AF/%E6%AF%8F%E6%97%A5%E7%9F%AD%E8%AE%AF-2026-10-27.html) |
| Weekly Digest | Weekly (Tue) | [知识萃取周刊 2026-10-W4](https://bor799.github.io/100x-research-engine/%E7%9F%A5%E8%AF%86%E8%90%83%E5%8F%96/%E7%9F%A5%E8%AF%86%E8%90%83%E5%8F%96%E5%91%A8%E5%88%8A%202026-10-W4.html) |
| Deep Read | Occasional | [2026-09-12 认知重编译-Grok Bot：AI产品的单位从对话变成同事](https://bor799.github.io/100x-research-engine/%E6%B7%B1%E8%AF%BB/2026-09-12%20%E8%AE%A4%E7%9F%A5%E9%87%8D%E7%BC%96%E8%AF%91-Grok%20Bot%EF%BC%9AAI%E4%BA%A7%E5%93%81%E7%9A%84%E5%8D%95%E4%BD%8D%E4%BB%8E%E5%AF%B9%E8%AF%9D%E5%8F%98%E6%88%90%E5%90%8C%E4%BA%8B.html) |

**[→ Open the magazine](https://bor799.github.io/100x-research-engine/)** (54 issues archived)
<!-- DEMO:END -->
