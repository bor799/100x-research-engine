"""Durable local queue for Cindy WeChat delivery.

Thin producer facade over :class:`WechatOutbox`. The pipeline calls ``deliver``
to drop a brief into ``pending/``; a Cindy scheduled task consumes from there
via the outbox CLI (claim / ack / nack).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path

from ..models import UTC, FetchedContent, TypedError, utc_now
from ..queue_store import FailureKind, NextAction
from .wechat_outbox import BUSINESS_LANE, OutboxItem, WechatOutbox, ttl_for_lane


class WechatQueue:
    """Write one brief per JSON file for Cindy to deliver later."""

    def __init__(self, queue_dir: Path) -> None:
        self.queue_dir = Path(queue_dir)
        self.outbox = WechatOutbox(self.queue_dir)

    def deliver(
        self,
        content: FetchedContent,
        text: str,
        *,
        lane: str = BUSINESS_LANE,
    ) -> tuple[str, str] | TypedError:
        """Atomically enqueue a brief into pending/ and return status + preview.

        ``lane`` ("business" | "strategic") records which digest schedule the
        consumer should attach this item to. See the V3 plan: business items go
        in the morning/evening digest, strategic items in the weekly one.
        """
        created_at = utc_now()
        slug = _slug(content.title) or content.content_hash[:12] or "brief"
        event_id = content.content_hash or f"{created_at}-{slug}"

        try:
            final_score = float(content.metadata.get("final_score", 0.0))
        except (TypeError, ValueError):
            final_score = 0.0
        try:
            business_story_fit = float(content.metadata.get("business_story_fit", 0.0))
        except (TypeError, ValueError):
            business_story_fit = 0.0
        try:
            score = float(content.metadata.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0

        item = OutboxItem(
            event_id=event_id,
            lane=lane,
            text=text.strip(),
            url=content.url,
            final_score=final_score,
            business_story_fit=business_story_fit,
            prompt_hash=str(content.metadata.get("prompt_hash", "")),
            created_at=created_at,
            expires_at=ttl_for_lane(lane),
            attempts=0,
            source=content.source,
        )

        try:
            enqueued = self.outbox.enqueue(item)
        except OSError as exc:
            return TypedError(
                failure_kind=FailureKind.OUTPUT_FAILED,
                message="Failed to enqueue WeChat brief",
                stage="output.wechat_queue",
                retryable=True,
                next_action=NextAction.RETRY_LATER,
                detail=str(exc),
            )

        return ("queued" if enqueued else "duplicate"), text.strip()[:80]


def _slug(value: str, *, max_length: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:max_length].rstrip("-")
