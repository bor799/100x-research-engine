# V2 Operations Lessons For V3

This is a reading note for future V3 implementation. It summarizes what V3 should inherit conceptually from V2 operations without copying V2 state, secrets, or process commands directly.

## Useful V2 Patterns

V2 has a central commander shape:

```text
Commander
  -> Bot Gateway
  -> Queue Worker
  -> Scheduler
  -> Health Check
```

V3 can reuse this role separation, but each role must run behind Runtime Guard and must use V3 paths only.

## Startup Experience

V2 `run.sh` teaches:

- resolve project-local Python first
- load env before starting
- set proxy env explicitly if needed
- expose simple commands: URL, RSS, add, start, stop, status, logs
- central daemon should spawn scheduler, bot, and queue worker

V3 improvement:

- do not use broad `pkill` by default
- never infer state root from a shadow HOME
- always print runtime fingerprint
- always pass `STATE_ROOT`, `QUEUE_DB_PATH`, and `LOG_PATH` explicitly to child roles

## Health Check Experience

V2 `startup_check.sh` teaches:

- check main DB exists
- detect running roles
- detect stale processing tasks
- recover stale tasks before restart
- only replace unhealthy components

V3 improvement:

- health check must inspect V3 queue schema before recovery
- stale task recovery should become `retry_scheduled`, not a blind reset
- process checks should match role-specific lock/fingerprint files
- restart should be role-specific
- admin notice should be emitted after live Telegram exists

## Queue Lessons

V2 queue states were too coarse. V3 must keep:

```text
pending
processing
retry_scheduled
done
rejected
failed_terminal
```

Rules:

- Fetch failure cannot be `done`.
- Output failure cannot be `done`.
- Rate limit should become `retry_scheduled`.
- Rejected and terminal failure must be distinguishable.

## Live Scheduler Lessons

The user wants the old nightly scheduled sources to run and write to Obsidian plus Telegram. In V3 this should happen only after:

- V3 source config is created.
- Runtime Guard validates V3 paths.
- dry-run single URL passes.
- staging scheduled run passes.
- Obsidian staging output is inspected.
- Telegram stub/test chat output is inspected.

Do not read V2 queue to create V3 scheduled work. If V2 source definitions are reused, copy only source URLs and categories into a V3 config file after removing credentials.

## Operator Commands For Future V3

Future commands should look like:

```bash
./scripts/run-v3.sh status
./scripts/run-v3.sh dry-run-url "https://example.com/article"
./scripts/run-v3.sh dry-run-schedule --limit 3
./scripts/run-v3.sh staging-schedule --limit 3
./scripts/run-v3.sh start
./scripts/run-v3.sh stop
```

`start` must refuse to run unless live mode is explicitly enabled in local config.
