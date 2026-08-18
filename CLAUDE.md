# Agent Handoff

This repository is the V4 implementation of the 100X knowledge extraction system (absorption-first rewrite of V3; the V3 behaviour is preserved under git tag `v3-final`). Treat it as the active product workspace, but do not assume every local-only secret or runtime file is publishable.

## Current State

- V4 core: single absorption call (`prompts/absorption.md`, 14-field JSON), code-owned weighted score `0.40×information_gain + 0.35×action_value + 0.25×relevance`, code-owned tier (A>=7.5 / B>=5.0 / C>=4.0 / Reject), spam hard-reject.
- Routing: `<0.40 or spam -> reject`; `0.40-0.74 -> archive_only`; `>=0.75 -> push` (action_value>=0.70 business lane, else strategic). Favored channels ride `routing.source_preferences` with a 400-char content floor.
- All LLM roles run GLM-5.2. One daemon role (`loop` = scan + work); Cindy owns WeChat delivery.
- Published scope intentionally excludes `.env`, `config/config.local.yaml`, cache files, `.DS_Store`, `ai-reading/`, and local staging output.

## Safety Rules

- Never publish `.env`, `config/config.local.yaml`, queue databases, logs, `.pytest_cache/`, `__pycache__/`, `.DS_Store`, or `ai-reading/`.
- Never point V3 at `.100x_v2`, `knowledge-extractor/v2`, V2 queues, or V2 config secrets.
- `RuntimeGuard` must reject V2 paths, shadow HOME paths, queue DBs outside `STATE_ROOT`, and incompatible queue schemas. It must not require the checkout directory to be named `v3`.
- Prefer `scripts/control.sh` for production control and `scripts/run-v3.sh` for low-level role commands.

## Verification Commands

```bash
python -m compileall -q src tests scripts
bash -n scripts/*.sh
python -m pytest -q
```

V4 baseline on 2026-08-17: `275 passed` after the single-call rewrite.

## Runtime And Config

- Default state root: `~/.100x_v3`
- Default queue DB: `~/.100x_v3/queue.db`
- Default log path: `~/100x-v3-daemon.log`
- Safe template: `config/config.example.yaml`
- Local secrets/config: `config/config.local.yaml` and `.env` only
- External RSS source registry: `config/sources.yaml`; `ConfigLoader` auto-loads it when no explicit `sources_files` is configured.
- Single daemon: `python -m knowledge_extractor_v3.daemon --loop` (scan + work); forensic scoring for one URL: `python scripts/score_traceback.py <url>`

## GitHub Tooling Note

Claude Code uses `@iflow-mcp/server-github`. That package reads `GITHUB_PERSONAL_ACCESS_TOKEN`, while older local docs/config used only `GITHUB_TOKEN`. Keep both env vars in `~/.mcp.json` and `~/.claude.json` for compatibility.

## Cindy 微信入口：URL 提交与状态查询

Cindy 微信会话是本系统的默认交互入口（替代 Telegram）。当在微信中收到用户消息时，按以下规则处理：

**收到 HTTP/HTTPS 链接时**：
1. 运行 `python3 scripts/cindy_control.py enqueue-url "<URL>"` 将链接加入处理队列
2. 将返回的 JSON（含 `task_id` 和 `status`）回复给用户，例如："✅ 已入队（task #123），系统会自动抓取、吸收、评分分层，完成后推送结果。"

**用户询问队列/系统状态时**（如"状态"、"queue"、"怎么样了"）：
1. 运行 `python3 scripts/cindy_control.py status` 获取队列概况
2. 或 `python3 scripts/cindy_control.py status --task-id <N>` 查询单条任务进度

**用户询问特定任务结果时**：
1. 运行 `python3 scripts/cindy_control.py status --task-id <N>` 查询
2. 如果 `status` 为 `done`，告知用户结果已存入 Obsidian 并会通过微信推送

这些命令输出确定性 JSON，不接触微信 connector 身份或 token。推送节奏由 Cindy schedule 控制（商业故事每天 8:30/11:30/17:20，战略周报每周日 17:20）。
