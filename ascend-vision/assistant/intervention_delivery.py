"""One local delivery gate for freshness, deduplication, and interruption budgets."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import threading
import time
import sqlite3
from pathlib import Path
from typing import Callable, Literal

from assistant.companion_policy import InterventionIntent


ReceiptStatus = Literal["suppressed", "claimed", "attempted", "failed", "expired"]


@dataclass(frozen=True)
class DeliveryReceipt:
    intent_id: str
    status: ReceiptStatus
    reason_code: str
    channel: str | None
    created_at: datetime


class InterventionDelivery:
    """Reserve each intent once and recheck its evidence immediately before speech."""

    def __init__(self, *, now=lambda: datetime.now(timezone.utc), monotonic=time.monotonic,
                 min_interval_seconds: int = 15 * 60,
                 max_per_hour: int = 3, max_per_day: int = 8,
                 receipt_path: str | Path | None = None):
        if type(min_interval_seconds) is not int or min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must be a nonnegative integer")
        if type(max_per_hour) is not int or max_per_hour <= 0:
            raise ValueError("max_per_hour must be a positive integer")
        if type(max_per_day) is not int or max_per_day <= 0:
            raise ValueError("max_per_day must be a positive integer")
        self._now = now
        self._monotonic = monotonic
        self.min_interval_seconds = min_interval_seconds
        self.max_per_hour = max_per_hour
        self.max_per_day = max_per_day
        self._receipts: dict[str, DeliveryReceipt] = {}
        self._attempts: deque[datetime] = deque()
        self._lock = threading.RLock()
        self._receipt_db = None
        if receipt_path is not None:
            path = Path(receipt_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._receipt_db = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
            self._receipt_db.execute(
                "CREATE TABLE IF NOT EXISTS DeliveredIntent (intentId TEXT PRIMARY KEY, createdAt REAL NOT NULL)"
            )
            self._receipt_db.execute(
                "DELETE FROM DeliveredIntent WHERE createdAt < ?", (time.time() - 30 * 24 * 60 * 60,)
            )
            self._receipt_db.commit()

    def dispatch(self, intent: InterventionIntent, *, revalidate: Callable[[], bool],
                 send: Callable[[str, Callable[[], bool]], bool]) -> DeliveryReceipt:
        if not isinstance(intent, InterventionIntent):
            raise TypeError("intent must be an InterventionIntent")
        if not callable(revalidate) or not callable(send):
            raise TypeError("revalidate and send must be callable")
        now = self._aware_now()
        monotonic_now = self._monotonic()
        if (isinstance(monotonic_now, bool) or not isinstance(monotonic_now, (int, float))
                or monotonic_now < 0):
            raise ValueError("delivery monotonic clock must return a nonnegative number")
        monotonic_now = float(monotonic_now)
        with self._lock:
            previous = self._receipts.get(intent.intent_id)
            if previous is not None:
                return previous
            if self._receipt_db is not None and self._was_previously_claimed(intent.intent_id):
                return self._store(intent, "suppressed", "duplicate_previous_run", None, now)
            if intent.expires_at.tzinfo is None or intent.expires_at.utcoffset() is None:
                return self._store(intent, "expired", "invalid_expiry", None, now)
            if now > intent.expires_at.astimezone(timezone.utc):
                return self._store(intent, "expired", "intent_expired", None, now)
            if "desktop_speech" not in intent.allowed_channels:
                return self._store(intent, "suppressed", "no_local_channel", None, now)
            if not self._safe_revalidate(revalidate):
                return self._store(intent, "suppressed", "conditions_changed", None, now)
            budget_reason = self._budget_reason(monotonic_now)
            if budget_reason is not None:
                return self._store(intent, "suppressed", budget_reason, None, now)
            if not self._claim_durable(intent.intent_id, now):
                return self._store(intent, "suppressed", "duplicate_or_store_unavailable", None, now)

            receipt = self._store(intent, "claimed", "reserved", "desktop_speech", now)
            self._attempts.append((monotonic_now, now))

        def delivery_guard() -> bool:
            checked_at = self._aware_now()
            with self._lock:
                current = self._receipts.get(intent.intent_id)
                if current is None or current.status not in {"claimed", "attempted"}:
                    return False
                if checked_at > intent.expires_at.astimezone(timezone.utc):
                    self._receipts[intent.intent_id] = DeliveryReceipt(
                        intent.intent_id, "expired", "intent_expired", "desktop_speech", checked_at,
                    )
                    return False
                if not self._safe_revalidate(revalidate):
                    self._receipts[intent.intent_id] = DeliveryReceipt(
                        intent.intent_id, "suppressed", "conditions_changed", "desktop_speech", checked_at,
                    )
                    return False
                return True

        try:
            accepted = bool(send(intent.message, delivery_guard))
        except Exception:
            accepted = False
        with self._lock:
            current = self._receipts[intent.intent_id]
            if current.status == "claimed":
                status: ReceiptStatus = "attempted" if accepted else "failed"
                reason = "queued" if accepted else "channel_unavailable"
                self._receipts[intent.intent_id] = DeliveryReceipt(
                    intent.intent_id, status, reason, "desktop_speech", self._aware_now(),
                )
            return self._receipts[intent.intent_id]

    def receipt(self, intent_id: str) -> DeliveryReceipt | None:
        with self._lock:
            existing = self._receipts.get(intent_id)
            if existing is not None:
                return existing
            if self._receipt_db is not None and self._was_previously_claimed(intent_id):
                receipt = DeliveryReceipt(intent_id, "suppressed", "duplicate_previous_run", None, self._aware_now())
                self._receipts[intent_id] = receipt
                return receipt
            return None

    def _was_previously_claimed(self, intent_id: str) -> bool:
        try:
            return self._receipt_db.execute(
                "SELECT 1 FROM DeliveredIntent WHERE intentId = ?", (intent_id,),
            ).fetchone() is not None
        except sqlite3.Error:
            return True

    def _claim_durable(self, intent_id: str, now: datetime) -> bool:
        if self._receipt_db is None:
            return True
        try:
            self._receipt_db.execute(
                "INSERT INTO DeliveredIntent(intentId, createdAt) VALUES (?, ?)",
                (intent_id, now.timestamp()),
            )
            self._receipt_db.commit()
            return True
        except (sqlite3.IntegrityError, sqlite3.Error):
            return False

    def close(self) -> None:
        with self._lock:
            if self._receipt_db is not None:
                self._receipt_db.close()
                self._receipt_db = None

    def _budget_reason(self, monotonic_now: float) -> str | None:
        hour_ago = monotonic_now - 60 * 60
        day_ago = monotonic_now - 24 * 60 * 60
        while self._attempts and self._attempts[0][0] < day_ago:
            self._attempts.popleft()
        if self._attempts and monotonic_now - self._attempts[-1][0] < self.min_interval_seconds:
            return "cooldown"
        hourly = sum(stamp >= hour_ago for stamp, _wall_time in self._attempts)
        if hourly >= self.max_per_hour:
            return "hourly_budget"
        if len(self._attempts) >= self.max_per_day:
            return "daily_budget"
        return None

    def _store(self, intent, status, reason, channel, created_at):
        receipt = DeliveryReceipt(intent.intent_id, status, reason, channel, created_at)
        self._receipts[intent.intent_id] = receipt
        return receipt

    @staticmethod
    def _safe_revalidate(callback: Callable[[], bool]) -> bool:
        try:
            return callback() is True
        except Exception:
            return False

    def _aware_now(self) -> datetime:
        value = self._now()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("delivery clock must return a timezone-aware datetime")
        return value.astimezone(timezone.utc)
