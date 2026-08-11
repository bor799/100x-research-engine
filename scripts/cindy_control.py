#!/usr/bin/env python3
"""Thin, deterministic control surface for Cindy-origin WeChat commands.

Cindy handles natural language and the authenticated chat. This script only
accepts explicit commands and returns JSON, so no connector identity or token
ever enters the 100X repository or queue database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.outputs.wechat_outbox import WechatOutbox
from knowledge_extractor_v3.queue_store import QueueStore
from knowledge_extractor_v3.runtime_guard import resolve_runtime_paths


def _loader_and_config():
    loader = ConfigLoader(project_root=PROJECT_ROOT)
    return loader, loader.load()


def _queue(args) -> QueueStore:
    if args.queue_db:
        return QueueStore(Path(args.queue_db).expanduser())
    loader, config = _loader_and_config()
    paths = resolve_runtime_paths(PROJECT_ROOT, config, loader, env=os.environ)
    return QueueStore(paths.queue_db_path)


def _outbox(args) -> WechatOutbox:
    if args.outbox_dir:
        return WechatOutbox(Path(args.outbox_dir).expanduser())
    loader, config = _loader_and_config()
    return WechatOutbox(loader.expand_path(config.outputs.wechat_queue_dir))


def cmd_enqueue_url(args) -> int:
    parsed = urlparse(args.url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        print(json.dumps({"ok": False, "error": "invalid_http_url"}))
        return 2
    task = _queue(args).enqueue(
        args.url.strip(),
        source="cindy_wechat",
        priority=args.priority,
    )
    print(json.dumps({
        "ok": True,
        "task_id": task.id,
        "status": task.status.value,
        "url": task.url,
    }, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_status(args) -> int:
    queue = _queue(args)
    if args.task_id is not None:
        try:
            task = queue.get_task(args.task_id)
        except KeyError:
            print(json.dumps({"ok": False, "error": "task_not_found", "task_id": args.task_id}))
            return 2
        payload = {
            "ok": True,
            "task_id": task.id,
            "status": task.status.value,
            "title": task.result_title,
            "failure_kind": task.failure_kind.value,
            "next_action": task.next_action.value,
            "updated_at": task.updated_at,
        }
    else:
        payload = {"ok": True, "queue": queue.count_by_status(), "outbox": _outbox(args).counts()}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="100X control commands for Cindy")
    parser.add_argument("--queue-db", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--outbox-dir", default=None, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    enqueue = sub.add_parser("enqueue-url", help="Enqueue one explicit HTTP(S) URL")
    enqueue.add_argument("url")
    enqueue.add_argument("--priority", type=int, default=50)
    enqueue.set_defaults(func=cmd_enqueue_url)

    status = sub.add_parser("status", help="Return queue/outbox or one task status as JSON")
    status.add_argument("--task-id", type=int, default=None)
    status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
