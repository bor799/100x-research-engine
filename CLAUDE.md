# Agent Handoff

This repository is the V4 implementation of the 100X knowledge extraction system (absorption-first rewrite of V3; the V3 behaviour is preserved under git tag `v3-final`). Treat it as the active product workspace, but do not assume every local-only secret or runtime file is publishable.

## Current State

- V4 core: single absorption call (`prompts/absorption.md`, 14-field JSON), code-owned weighted score `0.40×information_gain + 0.35×action_value + 0.25×relevance`, code-owned tier (A>=7.5 / B>=6.0 / Reject), spam hard-reject.
- Routing: `<0.60 or spam -> reject`; `0.60-0.74 -> archive_only` (cold storage: stays in the week folder, never enters the weekly magazine); `>=0.75 -> push` (action_value>=0.70 business lane, else strategic). The weekly magazine only carries push-band content (2026-08-30 operator decision; floor raised from 0.40). Favored channels ride `routing.source_preferences` with a 400-char content floor.
- All LLM roles run GLM-5.2. One daemon role (`loop` = scan + work); Cindy owns WeChat delivery.
- Story-identity dedup (2026-09-01): same story arriving via different transports (original RSS + aggregator digest + tracking-param mirrors) is deduplicated on the absorption card, not on URL/bytes. Three layers: URL-normalized queue `UNIQUE`, a post-absorption write-time gate (`dedup_outcome="duplicate_story"`, no second file), and a whole-vault self-healing pass in `dedupe_vault` (losers → `.trash-dedup/story/`, reading state re-keyed, manifest audit). Evidence-tier rule, precision-first; knobs under `dedup.story_*`. See `docs/STORY_IDENTITY_DEDUP.md`.
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

V4 baseline on 2026-08-17: `275 passed` after the single-call rewrite; on 2026-08-30 with the vault dedup layer (write guard, same-URL increments, periodic cleanup): `359 passed`; same evening with the reliability invariants (queue claim CAS + lease heartbeat + owner-guarded terminals, daemon singleton flock + SIGTERM, process-group stop, magazine bind retry, outbox TTL dead-letter on claim): `375 passed`; on 2026-09-01 with story-identity dedup (card-based cross-transport matching, write-time gate, whole-vault reconciliation, restore resurrection guard): `396 passed`. See `docs/RELIABILITY_INVARIANTS.md` and `docs/STORY_IDENTITY_DEDUP.md`.

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

**周刊阅读闭环**：新萃取不再写 `AI进展/`，也不再产生逐篇微信卡片。每篇进入 `信息源/YYYY-MM-WN/`，结构固定为「压缩萃取 → 阅读反馈/AI 复核托管区 → 原文」。单周 HTML `知识萃取周刊 YYYY-MM-WN.html` 每日追加并重建；阅读、无需评论、评论、划线和 AI 复核状态持久化在同周 JSON。localhost 服务默认 `127.0.0.1:8765`，便携 HTML 离线时只读。旧 outbox 继续排空，旧 `微信推送` 周账本与历史 `AI进展/` 不迁移。细节见 `docs/WEEKLY_MAGAZINE.md`。
