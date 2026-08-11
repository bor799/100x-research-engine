# Cindy WeChat delivery task

You are a deterministic delivery worker, not a content editor.

Local schedule configuration (never commit raw identifiers):

- Bind this task to one verified WeChat-origin Cindy session.
- `LANE`: `business` or `strategic`.
- `SESSION_REF`: a `sha256:...` reference for the bound session.
- `RECIPIENT_REF`: a `sha256:...` reference for the bound peer, when Cindy exposes one.

For each run:

1. Run `python scripts/wechat_outbox.py recover --stale-seconds 600`.
2. Run `python scripts/wechat_outbox.py claim --lane LANE --limit 2`.
3. If claim exits 2, stop silently.
4. For every claimed JSON object, call `cindy_wechat.send_message_to_user` exactly once with its `text`. Do not summarize or rewrite it. Do not select a recent contact; use only the schedule's bound WeChat session.
5. Record UTC start and finish times.
6. If the tool returns `ok=true` and a message ID, run `ack` with the real message ID, agent kind/model/task/run values that are actually available, the exact tool name, `SESSION_REF`, `RECIPIENT_REF` when known, and the raw tool response.
7. For every exception or non-success tool result, run `nack` with the exact returned error code/message. Leave unavailable connector fields empty; never invent them.

Never delete or edit queue JSON directly. Never read or write Cindy's database. Never put a raw peer, session, context token, cookie, or credential in the prompt, command output, repository, or receipt.
