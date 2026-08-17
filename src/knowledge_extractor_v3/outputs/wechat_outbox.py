"""Durable hand-off from 100X producers to Cindy's WeChat connector.

100X owns content production and a small file state machine. Cindy owns the
authenticated WeChat session and delivery tool.  The boundary is deliberately
boring: claim JSON, send ``text``, then persist a sanitized receipt with ack or
nack.  No Cindy database or connector token is read or written here.

Layout::

    <root>/
      pending/       waiting for Cindy
      processing/    claimed by one Cindy run
      sent/          delivery ledger (retained for 14 days)
      failed/        expired or exhausted after three attempts
      idempotency/   permanent event-id markers; never contain message text
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Mapping

MAX_ATTEMPTS = 3
SENT_RETENTION_DAYS = 14
BUSINESS_TTL_HOURS = 24
STRATEGIC_TTL_HOURS = 8 * 24
MAX_RAW_RESPONSE_CHARS = 2_048
MAX_RECEIPT_STRING_CHARS = 512

BUSINESS_LANE = "business"
STRATEGIC_LANE = "strategic"
STATES = ("pending", "processing", "sent", "failed")

_SECRET_KEY = re.compile(r"token|secret|password|authorization|cookie", re.I)
_REFERENCE_KEY = re.compile(r"recipient|session|peer|contact|user_id|wxid", re.I)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class OutboxItem:
    event_id: str
    lane: str
    text: str
    url: str
    final_score: float
    action_value: float
    prompt_hash: str
    created_at: str
    expires_at: str
    attempts: int
    state: str = "pending"
    enqueued_at: str = ""
    claimed_at: str = ""
    sent_at: str = ""
    failed_at: str = ""
    updated_at: str = ""
    delivery_attempts: tuple[dict[str, object], ...] = field(default_factory=tuple)
    # Feed source name from sources.yaml; used by claim() to spread a digest
    # window across sources. Empty for legacy payloads and manual enqueues.
    source: str = ""

    def to_payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "lane": self.lane,
            "text": self.text,
            "url": self.url,
            "final_score": self.final_score,
            "action_value": self.action_value,
            "prompt_hash": self.prompt_hash,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "attempts": self.attempts,
            "state": self.state,
            "enqueued_at": self.enqueued_at,
            "claimed_at": self.claimed_at,
            "sent_at": self.sent_at,
            "failed_at": self.failed_at,
            "updated_at": self.updated_at,
            "delivery_attempts": list(self.delivery_attempts),
            "source": self.source,
        }

    @classmethod
    def from_payload(cls, data: dict[str, object]) -> "OutboxItem":
        raw_attempts = data.get("delivery_attempts")
        delivery_attempts = tuple(
            item for item in raw_attempts if isinstance(item, dict)
        ) if isinstance(raw_attempts, list) else ()
        return cls(
            event_id=str(data.get("event_id") or ""),
            lane=str(data.get("lane") or BUSINESS_LANE),
            text=str(data.get("text") or ""),
            url=str(data.get("url") or ""),
            final_score=float(data.get("final_score") or 0.0),
            action_value=float(data.get("action_value", data.get("business_story_fit")) or 0.0),
            prompt_hash=str(data.get("prompt_hash") or ""),
            created_at=str(data.get("created_at") or ""),
            expires_at=str(data.get("expires_at") or ""),
            attempts=int(data.get("attempts") or 0),
            state=str(data.get("state") or "pending"),
            enqueued_at=str(data.get("enqueued_at") or ""),
            claimed_at=str(data.get("claimed_at") or ""),
            sent_at=str(data.get("sent_at") or ""),
            failed_at=str(data.get("failed_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
            delivery_attempts=delivery_attempts,
            source=str(data.get("source") or ""),
        )


class WechatOutbox:
    """Atomic, all-state-idempotent delivery state machine."""

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

    # -- producer ------------------------------------------------------

    def enqueue(self, item: OutboxItem) -> bool:
        """Write one pending event; return ``False`` for any prior event_id.

        A permanent, content-free marker closes both cross-state and concurrent
        replay races.  If the payload write fails, the marker is rolled back so
        the producer can retry safely.
        """
        if not item.event_id.strip():
            raise ValueError("event_id must not be empty")
        if self.find_state(item.event_id) is not None:
            return False

        marker = self._marker_path(item.event_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False

        timestamp = _now()
        payload = replace(
            item,
            state="pending",
            enqueued_at=item.enqueued_at or timestamp,
            updated_at=timestamp,
        ).to_payload()
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(
                    {"event_id_hash": self._event_hash(item.event_id), "created_at": timestamp},
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            self._atomic_write(self.root / "pending" / self._filename(item), payload)
        except Exception:
            marker.unlink(missing_ok=True)
            raise
        return True

    # -- consumer ------------------------------------------------------

    def claim(self, lane: str | None = None, limit: int = 1) -> list[OutboxItem]:
        """Move up to ``limit`` events to processing and start an attempt.

        Selection is round-robin by source: the first pass takes the best
        remaining item per source, so one prolific source cannot fill the whole
        digest window; a second pass backfills from skipped items when there
        are fewer distinct sources than slots. Items without a source fall back
        to their URL as the grouping key, so legacy/manual entries never
        collide.
        """
        pending = self._list("pending")
        if lane:
            pending = [item for item in pending if item.lane == lane]
        pending.sort(key=lambda item: (
            -item.final_score,
            item.created_at,
            item.event_id,  # deterministic order for same-second enqueues
        ))

        selected: list[OutboxItem] = []
        seen_sources: set[str] = set()
        skipped: list[OutboxItem] = []
        for item in pending:
            if len(selected) >= limit:
                break
            key = item.source or item.url
            if key in seen_sources:
                skipped.append(item)
                continue
            seen_sources.add(key)
            selected.append(item)
        for item in skipped:
            if len(selected) >= limit:
                break
            selected.append(item)

        claimed: list[OutboxItem] = []
        for item in selected:
            src = self.root / "pending" / self._filename(item)
            dst = self.root / "processing" / self._filename(item)
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dst)
            except FileNotFoundError:
                continue

            timestamp = _now()
            attempt_no = item.attempts + 1
            attempt = {
                "attempt": attempt_no,
                "status": "processing",
                "started_at": timestamp,
                "finished_at": "",
                "receipt": {},
            }
            updated = replace(
                item,
                state="processing",
                attempts=attempt_no,
                claimed_at=timestamp,
                updated_at=timestamp,
                delivery_attempts=(*item.delivery_attempts, attempt),
            )
            self._atomic_write(dst, updated.to_payload())
            claimed.append(updated)
        return claimed

    def ack(self, event_id: str, receipt: Mapping[str, object]) -> bool:
        """Persist a successful receipt and move processing to sent."""
        src = self._find("processing", event_id)
        if src is None:
            return False
        timestamp = _now()
        data = self._load_payload(src)
        normalized = sanitize_receipt(receipt)
        self._finish_attempt(data, "sent", normalized, timestamp)
        data.update({
            "state": "sent",
            "sent_at": timestamp,
            "updated_at": timestamp,
        })
        self._move_with_payload(src, self.root / "sent" / src.name, data)
        return True

    def nack(self, event_id: str, receipt: Mapping[str, object]) -> str:
        """Persist a failure and retry, moving the third failure to failed."""
        src = self._find("processing", event_id)
        if src is None:
            return "missing"
        timestamp = _now()
        data = self._load_payload(src)
        attempts = int(data.get("attempts") or 0)
        terminal = attempts >= self.max_attempts
        result_state = "failed" if terminal else "pending"
        attempt_status = "failed" if terminal else "retryable_failure"
        normalized = sanitize_receipt(receipt)
        self._finish_attempt(data, attempt_status, normalized, timestamp)
        data.update({"state": result_state, "updated_at": timestamp})
        if terminal:
            data["failed_at"] = timestamp
        self._move_with_payload(src, self.root / result_state / src.name, data)
        return result_state

    # -- inspection / maintenance ------------------------------------

    def find_state(self, event_id: str) -> str | None:
        for state in STATES:
            if self._find(state, event_id) is not None:
                return state
        if self._marker_path(event_id).exists():
            return "reaped"
        return None

    def counts(self) -> dict[str, int]:
        return {state: len(self._list(state)) for state in STATES}

    def expire(self, now: datetime | None = None) -> int:
        """Move expired pending events to failed without losing their ledger."""
        now = now or datetime.now(UTC)
        expired = 0
        for item in self._list("pending"):
            try:
                expires_at = datetime.fromisoformat(item.expires_at)
            except ValueError:
                ttl = self.business_ttl_hours if item.lane == BUSINESS_LANE else self.strategic_ttl_hours
                try:
                    expires_at = datetime.fromisoformat(item.created_at) + timedelta(hours=ttl)
                except ValueError:
                    continue
            if now <= expires_at:
                continue
            src = self.root / "pending" / self._filename(item)
            if not src.exists():
                continue
            timestamp = now.replace(microsecond=0).isoformat()
            data = self._load_payload(src)
            data.update({
                "state": "failed",
                "failed_at": timestamp,
                "updated_at": timestamp,
                "failure": {"code": "OUTBOX_EXPIRED", "message": "delivery TTL elapsed"},
            })
            self._move_with_payload(src, self.root / "failed" / src.name, data)
            expired += 1
        return expired

    def reap_sent(self, retention_days: int = SENT_RETENTION_DAYS) -> int:
        """Delete old sent payloads but retain idempotency markers."""
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        removed = 0
        for path in (self.root / "sent").glob("*.json"):
            try:
                data = self._load_payload(path)
                sent_at = datetime.fromisoformat(str(data.get("sent_at") or ""))
            except (ValueError, OSError, json.JSONDecodeError):
                continue
            if sent_at < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def recover_stale_processing(self, stale_seconds: int = 600) -> int:
        """Nack abandoned claims, counting them as delivery attempts."""
        cutoff = time.time() - stale_seconds
        recovered = 0
        for path in list((self.root / "processing").glob("*.json")):
            if path.stat().st_mtime >= cutoff:
                continue
            data = self._load_payload(path)
            event_id = str(data.get("event_id") or "")
            started_at = str(data.get("claimed_at") or "")
            result = self.nack(event_id, {
                "agent_context": {"agent_kind": "recovery"},
                "tool": "wechat_outbox.recover_stale_processing",
                "started_at": started_at,
                "finished_at": _now(),
                "error_code": "STALE_CLAIM_RECOVERED",
                "error_message": "consumer did not ack or nack before stale timeout",
                "raw_response": "",
            })
            if result != "missing":
                recovered += 1
        return recovered

    # -- internals -----------------------------------------------------

    def _list(self, state: str) -> list[OutboxItem]:
        items: list[OutboxItem] = []
        for path in (self.root / state).glob("*.json"):
            try:
                items.append(OutboxItem.from_payload(self._load_payload(path)))
            except (OSError, json.JSONDecodeError, ValueError, TypeError):
                continue
        return items

    def _find(self, state: str, event_id: str) -> Path | None:
        expected = self.root / state / f"{self._safe_event_id(event_id)}.json"
        if expected.exists():
            try:
                if str(self._load_payload(expected).get("event_id")) == event_id:
                    return expected
            except (OSError, json.JSONDecodeError):
                pass
        for path in (self.root / state).glob("*.json"):
            try:
                if str(self._load_payload(path).get("event_id")) == event_id:
                    return path
            except (OSError, json.JSONDecodeError):
                continue
        return None

    @staticmethod
    def _finish_attempt(
        data: dict[str, object],
        status: str,
        receipt: dict[str, object],
        finished_at: str,
    ) -> None:
        attempts = list(data.get("delivery_attempts") or [])
        if not attempts or not isinstance(attempts[-1], dict):
            raise ValueError("processing item has no active delivery attempt")
        current = dict(attempts[-1])
        current.update({
            "status": status,
            "finished_at": str(receipt.get("finished_at") or finished_at),
            "receipt": receipt,
        })
        attempts[-1] = current
        data["delivery_attempts"] = attempts

    def _move_with_payload(self, src: Path, dst: Path, payload: dict[str, object]) -> None:
        self._atomic_write(src, payload)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.replace(src, dst)

    @staticmethod
    def _load_payload(path: Path) -> dict[str, object]:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"outbox payload must be an object: {path}")
        return data

    def _marker_path(self, event_id: str) -> Path:
        return self.root / "idempotency" / f"{self._event_hash(event_id)}.json"

    @staticmethod
    def _event_hash(event_id: str) -> str:
        return hashlib.sha256(event_id.encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_event_id(event_id: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", event_id).strip("._")
        if not safe:
            safe = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
        return safe[:160]

    @classmethod
    def _filename(cls, item: OutboxItem) -> str:
        return f"{cls._safe_event_id(item.event_id or uuid.uuid4().hex)}.json"

    @staticmethod
    def _atomic_write(target: Path, payload: dict[str, object]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(".tmp-" + uuid.uuid4().hex + ".json")
        try:
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)


def hash_reference(value: object) -> str:
    """Return a stable non-reversible reference suitable for a public ledger."""
    text = str(value or "").strip()
    if not text:
        return ""
    if re.fullmatch(r"sha256:[0-9a-f]{12,64}", text):
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"sha256:{digest}"


def sanitize_receipt(receipt: Mapping[str, object]) -> dict[str, object]:
    """Bound and redact a Cindy/tool receipt before it reaches disk."""
    clean: dict[str, object] = {
        "agent_context": _sanitize_value(receipt.get("agent_context", {}), key="agent_context"),
        "tool": _bounded(receipt.get("tool")),
        "recipient_ref": hash_reference(receipt.get("recipient_ref")),
        "session_ref": hash_reference(receipt.get("session_ref")),
        "started_at": _bounded(receipt.get("started_at")),
        "finished_at": _bounded(receipt.get("finished_at")),
        "message_id": _bounded(receipt.get("message_id"), limit=160),
        "error_code": _bounded(receipt.get("error_code"), limit=160),
        "error_message": _bounded(receipt.get("error_message")),
        "raw_response": _sanitize_raw_response(receipt.get("raw_response", "")),
    }
    clean["observability_gaps"] = [
        field for field in ("recipient_ref", "session_ref") if not clean[field]
    ]
    return clean


def _bounded(value: object, *, limit: int = MAX_RECEIPT_STRING_CHARS) -> str:
    return str(value or "")[:limit]


def _sanitize_raw_response(value: object) -> object:
    clean = _sanitize_value(value, key="raw_response")
    encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True)
    if len(encoded) <= MAX_RAW_RESPONSE_CHARS:
        return clean
    return {"truncated": True, "preview": encoded[:MAX_RAW_RESPONSE_CHARS]}


def _sanitize_value(value: object, *, key: str) -> object:
    if _SECRET_KEY.search(key):
        return "[REDACTED]"
    if _REFERENCE_KEY.search(key) and key not in {"agent_context", "raw_response"}:
        return hash_reference(value)
    if isinstance(value, Mapping):
        return {
            _bounded(k, limit=80): _sanitize_value(v, key=str(k))
            for k, v in list(value.items())[:40]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, key=key) for item in value[:40]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded(value)


def ttl_for_lane(lane: str, *, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    hours = BUSINESS_TTL_HOURS if lane == BUSINESS_LANE else STRATEGIC_TTL_HOURS
    return (now + timedelta(hours=hours)).replace(microsecond=0).isoformat()
