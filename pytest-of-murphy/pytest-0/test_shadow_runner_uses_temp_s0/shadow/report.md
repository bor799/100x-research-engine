# V3 Shadow Run Report

## Run timestamp and environment paths

- Generated at: 2026-04-28T14:29:02+00:00
- Project root: `/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3`
- Shadow root: `/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow`
- State root: `/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow/.100x_v3`
- Queue DB path: `/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow/.100x_v3/queue.db`
- Staging Obsidian root: `/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow/obsidian-staging`
- Config path: `/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow.yaml`
- Active prompt bundle: `primary_market_v1`
- Parallel test bundles: `v2_legacy`
- Smoke passed: `False`

## Candidate count and status counts

- Attempted candidate executions: 1
- Unique candidate IDs: 1

- `done`: 1

## Output file list

- `/Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow/obsidian-staging/obsidian/2026-04-28-frontier-payments-api-finds-bottom-up-distribution-d6b6abf42eb6856f.md`

## Failed/rejected URL table

No failed or rejected URLs.

## Three best briefs

| ID | Status | Final score | Tier | Title | Output |
|---|---|---:|---|---|---|
| fixture-high | done | 0.46 | C | Frontier Payments API Finds Bottom-Up Distribution | /Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow/obsidian-staging/obsidian/2026-04-28-frontier-payments-api-finds-bottom-up-distribution-d6b6abf42eb6856f.md |

## Three worst briefs

| ID | Status | Final score | Tier | Title | Output |
|---|---|---:|---|---|---|
| fixture-high | done | 0.46 | C | Frontier Payments API Finds Bottom-Up Distribution | /Users/murphy/Documents/Obsidian Vault/职业发展/项目案例/100X_知识萃取系统/knowledge-extractor/v3/pytest-of-murphy/pytest-0/test_shadow_runner_uses_temp_s0/shadow/obsidian-staging/obsidian/2026-04-28-frontier-payments-api-finds-bottom-up-distribution-d6b6abf42eb6856f.md |

## Prompt adjustment notes

- The run used the real V3 prompt registry and prompt bundle wiring, but the Phase 3 runner deliberately used a shadow-only heuristic provider because no live LLM provider or secrets are present in V3.
- Replace the heuristic provider with a real provider only after adding explicit V3-only credentials and typed rate-limit handling.

## Whether to proceed to shadow schedule

Do not proceed to a shadow schedule yet; the smoke gate did not pass.
