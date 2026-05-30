# Agent Handoff

This repository is the V3 implementation of the 100X knowledge extraction system. Treat it as the active product workspace, but do not assume every local-only secret or runtime file is publishable.

## Current State

- Latest V3 code commits before this documentation cleanup:
  - `87924ed` fixes `RuntimeGuard` so a public checkout named `information-tracking-agent` is valid.
  - `ae4bca0` is the V3 product snapshot used for publishing.
- Draft PR: `https://github.com/bor799/information-tracking-agent/pull/1`
- PR branch: `codex/publish-v3-latest`
- Base branch: `main`
- Published scope intentionally excludes `.env`, `config/config.local.yaml`, cache files, `.DS_Store`, `ai-reading/`, and local staging output.

## Safety Rules

- Never publish `.env`, `config/config.local.yaml`, queue databases, logs, `.pytest_cache/`, `__pycache__/`, `.DS_Store`, or `ai-reading/`.
- Never point V3 at `.100x_v2`, `knowledge-extractor/v2`, V2 queues, or V2 config secrets.
- `RuntimeGuard` must reject V2 paths, shadow HOME paths, queue DBs outside `STATE_ROOT`, and incompatible queue schemas. It must not require the checkout directory to be named `v3`.
- Prefer `scripts/control.sh` for production control and `scripts/run-v3.sh` for low-level role commands.

## Verification Commands

```bash
python -m compileall -q src tests scripts/v2_compare.py scripts/test_rss_fetch.py
bash -n scripts/*.sh
python -m pytest -q
python scripts/test_rss_fetch.py --all --timeout 8 --target-rate 95
```

The 2026-05-30 publish verification passed compile, shell syntax, and `pytest` with `259 passed`. The RSS live fetch command was skipped by operator approval after a previous run hung without output; keep it as the remaining live-source-health gate.

## Runtime And Config

- Default state root: `~/.100x_v3`
- Default queue DB: `~/.100x_v3/queue.db`
- Default log path: `~/100x-v3-daemon.log`
- Safe template: `config/config.example.yaml`
- Local secrets/config: `config/config.local.yaml` and `.env` only
- External RSS source registry: `config/sources.yaml`; `ConfigLoader` auto-loads it when no explicit `sources_files` is configured.
- Daily report CLI: `100x-v3-us-ai-daily` or `python -m knowledge_extractor_v3.daily_reports.runner`

## GitHub Tooling Note

Claude Code uses `@iflow-mcp/server-github`. That package reads `GITHUB_PERSONAL_ACCESS_TOKEN`, while older local docs/config used only `GITHUB_TOKEN`. Keep both env vars in `~/.mcp.json` and `~/.claude.json` for compatibility.
