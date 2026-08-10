#!/usr/bin/env python3
"""CLI for the Cindy WeChat consumer to drive the durable outbox.

The Cindy scheduled task calls this instead of reasoning about file state
itself. The pre-run hook uses ``has-pending``: it exits with code 2 when there
is nothing to send, which lets the scheduler skip starting the agent entirely
(``silentWhenIdle``).

Usage::

    python scripts/wechat_outbox.py has-pending --lane business
    python scripts/wechat_outbox.py claim --lane business --limit 2
    python scripts/wechat_outbox.py ack <event_id>
    python scripts/wechat_outbox.py nack <event_id>
    python scripts/wechat_outbox.py list --lane business
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as a script without installing the package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.outputs.wechat_outbox import WechatOutbox


def _outbox(args) -> WechatOutbox:
    loader = ConfigLoader(project_root=PROJECT_ROOT)
    config = loader.load()
    queue_dir = loader.expand_path(config.outputs.wechat_queue_dir)
    return WechatOutbox(queue_dir)


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
    ok = box.ack(args.event_id)
    if not ok:
        print(f"event_id not found in processing: {args.event_id}", file=sys.stderr)
        return 1
    return 0


def cmd_nack(args) -> int:
    box = _outbox(args)
    result = box.nack(args.event_id)
    if result == "missing":
        print(f"event_id not found in processing: {args.event_id}", file=sys.stderr)
        return 1
    print(result)  # "pending" or "failed"
    return 0


def cmd_list(args) -> int:
    box = _outbox(args)
    items = box._list("pending")
    if args.lane:
        items = [it for it in items if it.lane == args.lane]
    items.sort(key=lambda it: (-it.business_story_fit, -it.final_score, it.created_at))
    for item in items:
        print(json.dumps(item.to_payload(), ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Cindy WeChat outbox consumer CLI")
    parser.add_argument("--queue-dir", default=None, help="Override the outbox root")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_lane(p):
        p.add_argument("--lane", default=None, choices=["business", "strategic"])

    p = sub.add_parser("has-pending", help="Exit 0 if pending items exist, 2 if empty")
    add_lane(p)
    p.set_defaults(func=cmd_has_pending)

    p = sub.add_parser("claim", help="Claim pending items (pending → processing)")
    add_lane(p)
    p.add_argument("--limit", type=int, default=2)
    p.set_defaults(func=cmd_claim)

    p = sub.add_parser("ack", help="Acknowledge a sent item (processing → sent)")
    p.add_argument("event_id")
    p.set_defaults(func=cmd_ack)

    p = sub.add_parser("nack", help="Return a claimed item (processing → pending/failed)")
    p.add_argument("event_id")
    p.set_defaults(func=cmd_nack)

    p = sub.add_parser("list", help="List pending items")
    add_lane(p)
    p.set_defaults(func=cmd_list)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
