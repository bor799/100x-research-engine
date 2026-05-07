#!/usr/bin/env python3
"""Reset failed extraction tasks to pending status for re-processing.

This script finds all tasks with failed_terminal or rejected status
and resets them to pending so they can be re-queued for extraction.
"""

import sqlite3
from datetime import timezone
from pathlib import Path

UTC = timezone.utc
DB_PATH = Path("/Users/murphy/.100x_v3/queue.db")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def reset_failed_tasks(
    dry_run: bool = True,
    include_retry_scheduled: bool = True,
    failure_kinds: list[str] | None = None,
) -> None:
    """Reset failed tasks to pending status.

    Args:
        dry_run: If True, only show what would be changed without making changes.
        include_retry_scheduled: Also reset retry_scheduled tasks.
        failure_kinds: Only reset specific failure kinds (None = all).
    """
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        # Build the query based on options
        statuses = ["failed_terminal", "rejected"]
        if include_retry_scheduled:
            statuses.append("retry_scheduled")

        status_filter = ",".join(f"'{s}'" for s in statuses)
        query = f"""
            SELECT id, url, status, failure_kind, last_error, attempt_count, next_retry_at
            FROM queue
            WHERE status IN ({status_filter})
        """
        params = []

        if failure_kinds:
            placeholders = ",".join("?" for _ in failure_kinds)
            query += f" AND failure_kind IN ({placeholders})"
            params.extend(failure_kinds)

        query += " ORDER BY id"

        cursor = conn.execute(query, params)
        failed_tasks = cursor.fetchall()

        if not failed_tasks:
            print("No failed tasks found in database.")
            return

        print(f"Found {len(failed_tasks)} tasks to reset:")
        print("-" * 80)

        for task_id, url, status, failure_kind, last_error, attempt_count, next_retry_at in failed_tasks:
            print(f"  [{task_id}] {url}")
            print(f"      Status: {status}, Failure: {failure_kind}, Attempts: {attempt_count}")
            if next_retry_at:
                print(f"      Next retry: {next_retry_at}")
            if last_error:
                error_preview = last_error[:100] + "..." if len(last_error) > 100 else last_error
                print(f"      Error: {error_preview}")
            print()

        if dry_run:
            print("-" * 80)
            print("DRY RUN - No changes made. Run with --execute to apply changes.")
            return

        # Reset the failed tasks to pending
        print("-" * 80)
        print("Resetting tasks to pending status...")

        status_filter = ",".join(f"'{s}'" for s in statuses)
        update_query = f"""
            UPDATE queue
            SET status = 'pending',
                attempt_count = 0,
                failure_kind = '',
                last_error = '',
                last_status_detail = '',
                next_action = '',
                next_retry_at = '',
                result_title = '',
                output_path = '',
                processed_at = '',
                updated_at = ?
            WHERE status IN ({status_filter})
        """
        update_params = [utc_now()]

        if failure_kinds:
            placeholders = ",".join("?" for _ in failure_kinds)
            update_query += f" AND failure_kind IN ({placeholders})"
            update_params.extend(failure_kinds)

        cursor = conn.execute(update_query, update_params)
        conn.commit()

        print(f"Reset {cursor.rowcount} tasks to pending status.")


def show_queue_stats() -> None:
    """Show current queue statistics."""
    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.execute(
            """
            SELECT status, COUNT(*) as count
            FROM queue
            GROUP BY status
            ORDER BY count DESC
            """
        )
        stats = dict(cursor.fetchall())

        print("\nQueue Statistics:")
        print("-" * 40)
        for status, count in stats.items():
            print(f"  {status:20} {count:>6}")
        print("-" * 40)
        print(f"  {'TOTAL':20} {sum(stats.values()):>6}")


if __name__ == "__main__":
    import argparse

    from datetime import datetime

    parser = argparse.ArgumentParser(
        description="Reset failed extraction tasks to pending status"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually execute the reset (default is dry-run)",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Show queue statistics only",
    )
    parser.add_argument(
        "--no-retry-scheduled",
        action="store_true",
        help="Do not include retry_scheduled tasks (only reset failed_terminal and rejected)",
    )
    parser.add_argument(
        "--failure-kind",
        action="append",
        help="Only reset tasks with specific failure kind (can be used multiple times)",
    )

    args = parser.parse_args()

    if args.stats:
        show_queue_stats()
    else:
        reset_failed_tasks(
            dry_run=not args.execute,
            include_retry_scheduled=not args.no_retry_scheduled,
            failure_kinds=args.failure_kind,
        )
        show_queue_stats()
