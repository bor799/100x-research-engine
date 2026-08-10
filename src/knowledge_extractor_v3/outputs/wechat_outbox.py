"""Durable WeChat outbox for Cindy-driven delivery.

The producer (pipeline) drops briefs into ``pending/``. A Cindy scheduled task is
the consumer: it atomically *claims* an item (pending → processing), sends it via
WeChat, then *acks* (processing → sent) or *nacks* (processing → pending, with a
retry count; after ``max_attempts`` it moves to ``failed/``).

This replaces the old "write a flat JSON, delete on send" pattern, which had no
ledger, no claim/ack contract, and lost items if the consumer crashed mid-send.

Layout::

    <root>/
      pending/      items waiting to be sent
      processing/   items a consumer has claimed
      sent/         delivered items (kept 14 days for the ledger)
      failed/       items that exhausted retries

Expiry: business items not sent within 24h and strategic items not sent within
8 days are expired out of pending (the Obsidian archive still keeps them).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ..models import utc_now

MAX_ATTEMPTS = 3
SENT_RETENTION_DAYS = 14
BUSINESS_TTL_HOURS = 24
STRATEGIC_TTL_HOURS = 8 * 24

BUSINESS_LANE = "business"
STRATEGIC_LANE = "strategic"


@dataclass(frozen=True)
class OutboxItem:
    event_id: str
    lane: str
    text: str
    url: str
    final_score: float
    business_story_fit: float
    prompt_hash: str
    created_at: str
    expires_at: str
    attempts: int

    def to_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "lane": self.lane,
            "text": self.text,
            "url": self.url,
            "final_score": self.final_score,
            "business_story_fit": self.business_story_fit,
            "prompt_hash": self.prompt_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "attempts": self.attempts,
        }

    @classmethod
    def from_payload(cls, data: dict[str, object]) -> "OutboxItem":
        return cls(
            event_id=str(data.get("event_id") or ""),
            lane=str(data.get("lane") or BUSINESS_LANE),
            text=str(data.get("text") or ""),
            url=str(data.get("url") or ""),
            final_score=float(data.get("final_score") or 0.0),
            business_story_fit=float(data.get("business_story_fit") or 0.0),
            prompt_hash=str(data.get("prompt_hash") or ""),
            created_at=str(data.get("created_at") or ""),
            expires_at=str(data.get("expires_at") or ""),
            attempts=int(data.get("attempts") or 0),
        )


class WechatOutbox:
    """File-based durable outbox with atomic state transitions.

    All state changes use temp-file + ``os.replace`` (rename) so a crash mid-move
    leaves the item in exactly one directory. event_id dedupes across pending.
    """

    def __init__(
        self,
        root: Path,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        business_ttl_hours: int = BUSINESS_TTL_HOURS,
        strategic_ttl_hours: int = STRATEGIC_TTL_HOURS,
    ) -> None:
        self.root = Path(root)
        self.max_attempts = max_attempts
        self.business_ttl_hours = business_ttl_hours
        self.strategic_ttl_hours = strategic_ttl_hours
        # Directories are created lazily by _atomic_write / the state moves, so
        # constructing an outbox whose root is currently a file (an operator
        # misconfiguration) does not crash here — it surfaces as a TypedError
        # at the first write, where the caller already handles OSError.

    # -- producer ----------------------------------------------------------

    def enqueue(self, item: OutboxItem) -> bool:
        """Atomically write an item to pending/. Returns False on event_id dup."""
        target = self.root / "pending" / self._filename(item)
        if target.exists():
            return False
        self._atomic_write(target, item.to_payload())
        return True

    # -- consumer ----------------------------------------------------------

    def claim(self, lane: str | None = None, limit: int = 1) -> list[OutboxItem]:
        """Atomically move up to ``limit`` pending items into processing/.

        Items are sorted within lane: business by business_story_fit desc, then
        final_score desc, then oldest; strategic by final_score desc. An optional
        ``lane`` filter restricts to one digest lane.
        """
        pending = self._list("pending")
        if lane:
            pending = [it for it in pending if it.lane == lane]
        if not pending:
            return []

        pending.sort(key=lambda it: (
            -it.business_story_fit,
            -it.final_score,
            it.created_at,
        ))

        claimed: list[OutboxItem] = []
        for item in pending[:limit]:
            src = self.root / "pending" / self._filename(item)
            dst = self.root / "processing" / self._filename(item)
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
                claimed.append(item)
            except FileNotFoundError:
                continue  # raced with another consumer
        return claimed

    def ack(self, event_id: str) -> bool:
        """Mark a claimed item as sent (processing → sent)."""
        src = self._find("processing", event_id)
        if src is None:
            return False
        dst = self.root / "sent" / src.name
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)
        return True

    def nack(self, event_id: str) -> str:
        """Return a claimed item. After max_attempts, move to failed/.

        Returns the resulting state: ``"pending"`` or ``"failed"``.
        """
        src = self._find("processing", event_id)
        if src is None:
            return "missing"
        data = json.loads(src.read_text(encoding="utf-8"))
        attempts = int(data.get("attempts") or 0) + 1
        data["attempts"] = attempts

        if attempts >= self.max_attempts:
            dst = self.root / "failed" / src.name
        else:
            dst = self.root / "pending" / src.name
        self._atomic_write(dst, data)
        src.unlink(missing_ok=True)
        return "failed" if attempts >= self.max_attempts else "pending"

    # -- maintenance -------------------------------------------------------

    def expire(self, now: datetime | None = None) -> int:
        """Remove expired items from pending. Returns the count expired."""
        now = now or datetime.now(UTC)
        expired = 0
        for item in self._list("pending"):
            ttl = (
                self.business_ttl_hours
                if item.lane == BUSINESS_LANE
                else self.strategic_ttl_hours
            )
            try:
                created = datetime.fromisoformat(item.created_at)
            except ValueError:
                continue
            if now - created > timedelta(hours=ttl):
                (self.root / "pending" / self._filename(item)).unlink(missing_ok=True)
                expired += 1
        return expired

    def reap_sent(self, retention_days: int = SENT_RETENTION_DAYS) -> int:
        """Delete sent items older than the retention window."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        removed = 0
        for path in (self.root / "sent").glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                created = datetime.fromisoformat(str(data.get("created_at") or ""))
            except (ValueError, OSError):
                continue
            if created < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def recover_stale_processing(self, stale_seconds: int = 600) -> int:
        """Return processing items whose mtime is older than stale_seconds to pending."""
        cutoff = time.time() - stale_seconds
        recovered = 0
        for path in (self.root / "processing").glob("*.json"):
            if path.stat().st_mtime < cutoff:
                data = json.loads(path.read_text(encoding="utf-8"))
                dst = self.root / "pending" / path.name
                self._atomic_write(dst, data)
                path.unlink(missing_ok=True)
                recovered += 1
        return recovered

    # -- internals ---------------------------------------------------------

    def _list(self, state: str) -> list[OutboxItem]:
        items: list[OutboxItem] = []
        for path in (self.root / state).glob("*.json"):
            try:
                items.append(OutboxItem.from_payload(
                    json.loads(path.read_text(encoding="utf-8"))
                ))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
        return items

    def _find(self, state: str, event_id: str) -> Path | None:
        for path in (self.root / state).glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if str(data.get("event_id")) == event_id:
                return path
        return None

    @staticmethod
    def _filename(item: OutboxItem) -> str:
        safe = (item.event_id or uuid.uuid4().hex).replace("/", "_")
        return f"{safe}.json"

    @staticmethod
    def _atomic_write(target: Path, payload: dict[str, object]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(".tmp-" + uuid.uuid4().hex + ".json")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)


def ttl_for_lane(lane: str, *, now: datetime | None = None) -> str:
    """ISO expiry timestamp for a freshly enqueued item in the given lane."""
    now = now or datetime.now(UTC)
    hours = BUSINESS_TTL_HOURS if lane == BUSINESS_LANE else STRATEGIC_TTL_HOURS
    return (now + timedelta(hours=hours)).replace(microsecond=0).isoformat()
