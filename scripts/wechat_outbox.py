#!/usr/bin/env python3
"""CLI for the Cindy WeChat consumer to drive the durable outbox.

The Cindy scheduled task calls this instead of reasoning about file state
itself. The pre-run hook uses ``has-pending``: it exits with code 2 when there
is nothing to send, which lets the scheduler skip starting the agent entirely
(``silentWhenIdle``).

Usage::

    python scripts/wechat_outbox.py has-pending --lane business
    python scripts/wechat_outbox.py claim --lane business --limit 2
    python scripts/wechat_outbox.py ack <event_id> <receipt options>
    python scripts/wechat_outbox.py nack <event_id> <receipt options>
    python scripts/wechat_outbox.py release <event_id> <receipt options>
    python scripts/wechat_outbox.py requeue-failed --reason "channel recovered" [--lane business] [--event-id ID]...
    python scripts/wechat_outbox.py list --lane business
    python scripts/wechat_outbox.py status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Allow running as a script without installing the package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.outputs.push_ledger import PushLedger
from knowledge_extractor_v3.outputs.wechat_outbox import OutboxItem, WechatOutbox


def _outbox(args) -> WechatOutbox:
    if args.queue_dir:
        return WechatOutbox(Path(args.queue_dir).expanduser())
    loader = ConfigLoader(project_root=PROJECT_ROOT)
    config = loader.load()
    queue_dir = loader.expand_path(config.outputs.wechat_queue_dir)
    return WechatOutbox(queue_dir)


def _ledger(args) -> PushLedger | None:
    """Local landing ledger, or None when not configured.

    Resolution order: ``PUSH_LEDGER_DIR`` env override, then
    ``outputs.obsidian_root`` from config — but only when the outbox itself
    is the configured production one. A ``--queue-dir`` override selects a
    sandbox outbox (tests, drills); without an explicit ledger dir such runs
    must never touch the real vault. Landing is best-effort; delivery
    receipts in the outbox stay the source of truth.
    """
    env_dir = os.environ.get("PUSH_LEDGER_DIR")
    if env_dir:
        return PushLedger(Path(env_dir).expanduser())
    if getattr(args, "queue_dir", None):
        return None
    loader = ConfigLoader(project_root=PROJECT_ROOT)
    config = loader.load()
    if not str(config.outputs.obsidian_root).strip():
        return None
    root = loader.expand_path(config.outputs.obsidian_root)
    return PushLedger(root, notes_subdir=config.outputs.obsidian_subdir)


def cmd_has_pending(args) -> int:
    """Exit 0 if items are waiting, 2 if empty (so the scheduler skips)."""
    box = _outbox(args)
    items = [it for it in box._list("pending") if (args.lane is None or it.lane == args.lane)]
    return 0 if items else 2


def cmd_claim(args) -> int:
    box = _outbox(args)
    items = box.claim(lane=args.lane, limit=args.limit)
    if not items:
        return 2
    for item in items:
        print(json.dumps(item.to_payload(), ensure_ascii=False))
    return 0


def cmd_ack(args) -> int:
    box = _outbox(args)
    ok = box.ack(args.event_id, _receipt(args))
    if not ok:
        print(f"event_id not found in processing: {args.event_id}", file=sys.stderr)
        return 1
    _land_sent_item(args, box, args.event_id)
    return 0


def _land_sent_item(args, box: WechatOutbox, event_id: str) -> None:
    """Mirror an acked card into the local vault ledger (best-effort)."""
    ledger = _ledger(args)
    if ledger is None:
        return
    sent = box._find("sent", event_id)
    if sent is None:
        return
    try:
        item = OutboxItem.from_payload(box._load_payload(sent))
    except (OSError, ValueError):
        return
    outcome = ledger.land(item)
    if outcome.status == "error":
        print(
            f"ledger landing failed for {event_id}: {outcome.detail}",
            file=sys.stderr,
        )
    else:
        print(json.dumps({
            "landed": outcome.status == "landed",
            "ledger_path": str(outcome.path or ""),
            "event_id": event_id,
        }, ensure_ascii=False))


def cmd_land(args) -> int:
    """Backfill the vault ledger from outbox sent/ payloads."""
    box = _outbox(args)
    ledger = _ledger(args)
    if ledger is None:
        print(
            "ledger not configured: set PUSH_LEDGER_DIR, or run without "
            "--queue-dir so config outputs.obsidian_root applies",
            file=sys.stderr,
        )
        return 1
    items = box._list("sent")
    if args.lane:
        items = [it for it in items if it.lane == args.lane]
    items.sort(key=lambda it: (it.sent_at or it.created_at, it.event_id))
    landed, duplicates, errors = 0, 0, []
    last_path = ""
    for item in items:
        outcome = ledger.land(item)
        if outcome.status == "landed":
            landed += 1
            last_path = str(outcome.path)
        elif outcome.status == "duplicate":
            duplicates += 1
        else:
            errors.append({"event_id": item.event_id, "detail": outcome.detail})
    print(json.dumps({
        "landed": landed,
        "duplicates": duplicates,
        "errors": errors,
        "ledger_path": last_path,
    }, ensure_ascii=False))
    return 0 if (landed or duplicates) else 2


def cmd_nack(args) -> int:
    box = _outbox(args)
    result = box.nack(args.event_id, _receipt(args))
    if result == "missing":
        print(f"event_id not found in processing: {args.event_id}", file=sys.stderr)
        return 1
    print(result)  # "pending" or "failed"
    return 0


def cmd_release(args) -> int:
    box = _outbox(args)
    result = box.release(args.event_id, _receipt(args))
    if result == "missing":
        print(f"event_id not found in processing: {args.event_id}", file=sys.stderr)
        return 1
    print(result)  # "pending"
    return 0


def cmd_requeue_failed(args) -> int:
    box = _outbox(args)
    summary = box.requeue_failed(
        reason=args.reason,
        lane=args.lane,
        event_ids=tuple(args.event_id or ()),
        refresh_ttl=not args.no_refresh_ttl,
        include_all=args.all,
    )
    print(json.dumps({
        "requeued": list(summary.requeued),
        "skipped": [
            {"event_id": event_id, "cause": cause}
            for event_id, cause in summary.skipped
        ],
    }, ensure_ascii=False))
    return 0 if summary.requeued else 2


def cmd_list(args) -> int:
    box = _outbox(args)
    items = box._list("pending")
    if args.lane:
        items = [it for it in items if it.lane == args.lane]
    items.sort(key=lambda it: (-it.final_score, it.created_at))
    for item in items:
        print(json.dumps(item.to_payload(), ensure_ascii=False))
    return 0


def cmd_status(args) -> int:
    box = _outbox(args)
    print(json.dumps(box.counts(), sort_keys=True))
    return 0


def cmd_recover(args) -> int:
    box = _outbox(args)
    print(box.recover_stale_processing(stale_seconds=args.stale_seconds))
    return 0


def _receipt(args) -> dict[str, object]:
    agent_context = {
        key: value
        for key, value in {
            "agent_kind": args.agent_kind,
            "model": args.model,
            "task_id": args.task_id,
            "run_id": args.run_id,
        }.items()
        if value
    }
    raw_response: object = args.raw_response
    if args.raw_response:
        try:
            raw_response = json.loads(args.raw_response)
        except json.JSONDecodeError:
            pass
    return {
        "agent_context": agent_context,
        "tool": args.tool,
        "recipient_ref": args.recipient_ref,
        "session_ref": args.session_ref,
        "started_at": args.started_at,
        "finished_at": args.finished_at,
        "message_id": getattr(args, "message_id", ""),
        "error_code": getattr(args, "error_code", ""),
        "error_message": getattr(args, "error_message", ""),
        "raw_response": raw_response,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cindy WeChat outbox consumer CLI")
    parser.add_argument("--queue-dir", default=None, help="Override the outbox root")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_lane(p):
        p.add_argument("--lane", default=None, choices=["business", "strategic"])

    def add_receipt(p, *, success: bool):
        p.add_argument("--agent-kind", required=True)
        p.add_argument("--model", default="")
        p.add_argument("--task-id", default="")
        p.add_argument("--run-id", default="")
        p.add_argument("--tool", required=True)
        p.add_argument("--recipient-ref", default="")
        p.add_argument("--session-ref", default="")
        p.add_argument("--started-at", required=True)
        p.add_argument("--finished-at", required=True)
        p.add_argument("--raw-response", default="")
        if success:
            p.add_argument("--message-id", required=True)
        else:
            p.add_argument("--error-code", default="")
            p.add_argument("--error-message", required=True)

    p = sub.add_parser("has-pending", help="Exit 0 if pending items exist, 2 if empty")
    add_lane(p)
    p.set_defaults(func=cmd_has_pending)

    p = sub.add_parser("claim", help="Claim pending items (pending → processing)")
    add_lane(p)
    p.add_argument("--limit", type=int, default=2)
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("ack", help="Acknowledge a sent item (processing → sent)")
    p.add_argument("event_id")
    add_receipt(p, success=True)
    p.set_defaults(func=cmd_ack)

    p = sub.add_parser("nack", help="Return a claimed item (processing → pending/failed)")
    p.add_argument("event_id")
    add_receipt(p, success=False)
    p.set_defaults(func=cmd_nack)

    p = sub.add_parser(
        "release",
        help="Return a claimed item without consuming an attempt (processing → pending)",
    )
    p.add_argument("event_id")
    add_receipt(p, success=False)
    p.set_defaults(func=cmd_release)

    p = sub.add_parser(
        "requeue-failed",
        help="Move delivery-failed items back to pending (content rejections skipped by default)",
    )
    add_lane(p)
    p.add_argument("--reason", required=True, help="Why the items are being requeued")
    p.add_argument("--event-id", action="append", default=None, help="Requeue a specific id; overrides filters")
    p.add_argument("--all", action="store_true", help="Include content-blocked and expired items")
    p.add_argument("--no-refresh-ttl", action="store_true", help="Keep the original expires_at")
    p.set_defaults(func=cmd_requeue_failed)

    p = sub.add_parser("list", help="List pending items")
    add_lane(p)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser(
        "land",
        help="Backfill the local vault ledger from sent items (idempotent)",
    )
    add_lane(p)
    p.set_defaults(func=cmd_land)

    p = sub.add_parser("status", help="Print counts for all outbox states")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("recover", help="Nack stale processing claims")
    p.add_argument("--stale-seconds", type=int, default=600)
    p.set_defaults(func=cmd_recover)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
