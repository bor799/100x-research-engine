#!/usr/bin/env python3
"""监控任务完成情况的脚本。

用于检查特定任务是否已完成深度萃取，输出文件是否完整。

用法:
    python scripts/monitor_completion.py --task-ids 3,4,5,6,7,8
    python scripts/monitor_completion.py --watch  # 持续监控
"""

import argparse
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB = "/Users/murphy/.100x_v3/queue.db"
MIN_COMPLETE_LINES = 40  # 完整任务的最少行数


def get_task_status(task_id: int, cur: sqlite3.Cursor) -> dict:
    """获取任务状态详情。"""
    cur.execute("""
        SELECT id, url, status, output_path, attempt_count, processed_at
        FROM queue WHERE id = ?
    """, (task_id,))
    row = cur.fetchone()

    if not row:
        return {"exists": False}

    task_id, url, status, output_path, attempt_count, processed_at = row

    result = {
        "exists": True,
        "id": task_id,
        "url": url,
        "status": status,
        "output_path": output_path,
        "attempt_count": attempt_count,
        "processed_at": processed_at,
    }

    # 检查输出文件
    if output_path and not output_path.startswith("dry-run://"):
        if os.path.exists(output_path):
            with open(output_path, 'r') as f:
                lines = len(f.readlines())
            result["output_exists"] = True
            result["output_lines"] = lines
            result["is_complete"] = lines >= MIN_COMPLETE_LINES
        else:
            result["output_exists"] = False
            result["is_complete"] = False
    else:
        result["output_exists"] = False
        result["is_complete"] = False

    return result


def print_status(status: dict) -> None:
    """打印任务状态。"""
    if not status.get("exists"):
        print(f"  ❌ 任务不存在")
        return

    status_icon = {
        "pending": "⏳",
        "processing": "🔄",
        "done": "✅",
        "retry_scheduled": "🔁",
        "failed_terminal": "❌",
    }.get(status["status"], "❓")

    print(f"  {status_icon} ID {status['id']}: {status['status']}")

    if status.get("output_exists"):
        lines = status["output_lines"]
        complete = "✓ 完整" if status["is_complete"] else f"⚠️ 不完整 ({lines} 行)"
        print(f"     📄 输出: {complete}")
    elif status["status"] == "done":
        print(f"     ⚠️ 已完成但无有效输出")


def monitor_task_ids(task_ids: list[int], watch: bool = False, interval: int = 30) -> None:
    """监控指定任务。"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    try:
        iteration = 0
        while True:
            os.system('clear' if os.name == 'posix' else 'cls')

            print(f"=== 任务完成监控 ===")
            print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            if watch:
                print(f"监控中... (Ctrl+C 退出)")
            print()

            all_complete = True
            for task_id in task_ids:
                status = get_task_status(task_id, cur)
                print_status(status)
                if not status.get("is_complete") and status.get("status") != "done":
                    all_complete = False
                elif status.get("status") == "done" and not status.get("is_complete"):
                    all_complete = False

            print()
            if all_complete:
                print("🎉 所有任务已完成！")
                break
            else:
                pending = sum(1 for tid in task_ids if get_task_status(tid, cur).get("status") in ("pending", "processing", "retry_scheduled"))
                print(f"⏳ 待处理/进行中: {pending}/{len(task_ids)}")

            if not watch:
                break

            iteration += 1
            time.sleep(interval)

    finally:
        conn.close()


def list_incomplete() -> None:
    """列出所有未完成闭环的任务。"""
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    cur.execute("""
        SELECT id, url, status, output_path
        FROM queue
        WHERE status = 'done'
        ORDER BY id
    """)

    incomplete = []
    for row in cur.fetchall():
        task_id, url, status, output_path = row
        if not output_path or output_path.startswith("dry-run://"):
            continue

        if os.path.exists(output_path):
            with open(output_path, 'r') as f:
                lines = len(f.readlines())
            if lines < MIN_COMPLETE_LINES:
                incomplete.append((task_id, url, lines))

    if incomplete:
        print("=== 未完成闭环的任务 ===")
        for task_id, url, lines in incomplete:
            print(f"ID {task_id}: {lines} lines | {url[:70]}...")
        print(f"\n总计: {len(incomplete)} 个")
    else:
        print("✓ 所有任务均已完成闭环")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="监控任务完成情况")
    parser.add_argument("--task-ids", type=str, help="要监控的任务ID，逗号分隔")
    parser.add_argument("--watch", action="store_true", help="持续监控模式")
    parser.add_argument("--interval", type=int, default=30, help="监控刷新间隔（秒）")
    parser.add_argument("--list-incomplete", action="store_true", help="列出未完成闭环的任务")

    args = parser.parse_args()

    if args.list_incomplete:
        list_incomplete()
    elif args.task_ids:
        task_ids = [int(x.strip()) for x in args.task_ids.split(",")]
        monitor_task_ids(task_ids, watch=args.watch, interval=args.interval)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
