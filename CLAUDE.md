# Agent Handoff

This repository is the V3 implementation of the 100X knowledge extraction system. Treat it as the active product workspace, but do not assume every local-only secret or runtime file is publishable.

## Current State

- Latest V3 code commits before this documentation cleanup:
  - `87924ed` fixes `RuntimeGuard` so a public checkout named `information-tracking-agent` is valid.
  - `ae4bca0` is the V3 product snapshot used for publishing.
- Draft PR: `https://github.com/bor799/information-tracking-agent/pull/1`
- PR branch: `claude code/publish-v3-latest`
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

## Cindy 微信入口：URL 提交与状态查询

Cindy 微信会话是本系统的默认交互入口（替代 Telegram）。当在微信中收到用户消息时，按以下规则处理：

**收到 HTTP/HTTPS 链接时**：
1. 运行 `python3 scripts/cindy_control.py enqueue-url "<URL>"` 将链接加入处理队列
2. 将返回的 JSON（含 `task_id` 和 `status`）回复给用户，例如："✅ 已入队（task #123），系统会自动抓取、评分、萃取，完成后推送结果。"

**用户询问队列/系统状态时**（如"状态"、"queue"、"怎么样了"）：
1. 运行 `python3 scripts/cindy_control.py status` 获取队列概况
2. 或 `python3 scripts/cindy_control.py status --task-id <N>` 查询单条任务进度

**用户询问特定任务结果时**：
1. 运行 `python3 scripts/cindy_control.py status --task-id <N>` 查询
2. 如果 `status` 为 `done`，告知用户结果已存入 Obsidian 并会通过微信推送

这些命令输出确定性 JSON，不接触微信 connector 身份或 token。推送节奏由 Cindy schedule 控制（商业故事每天 8:30/18:30，战略周报每周日 18:40）。
