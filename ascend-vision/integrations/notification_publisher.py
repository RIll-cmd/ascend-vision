"""Nonblocking, opt-in publisher for one privacy-minimal agent-input event."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import logging
import os
import queue
import threading
import time
from urllib.parse import urlsplit

import httpx

LOG = logging.getLogger(__name__)


def build_notification_payload(intent, *, now: datetime | None = None) -> dict | None:
    """Only explicit producer needs-input events may leave Vision; omit task labels/details."""
    if getattr(intent, "rule_id", None) != "agent_needs_input":
        return None
    if not isinstance(intent.deduplication_key, str) or not intent.deduplication_key:
        return None
    checked = now or datetime.now(timezone.utc)
    if checked.tzinfo is None or checked.utcoffset() is None:
        raise ValueError("notification publisher clock must be timezone-aware")
    trigger = "agent-input:" + hashlib.sha256(intent.deduplication_key.encode("utf-8")).hexdigest()
    return {
        "triggerKey": trigger,
        "category": "Agent attention",
        "detail": "A connected AI agent requested your input. Open Ascend Vision to check its latest status.",
        "expiresAt": (checked.astimezone(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }


class CoreNotificationPublisherClient:
    def __init__(self, base_url: str, publisher_token: str, *, forbidden_credentials=(), transport=None):
        parts = urlsplit(base_url.strip())
        if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("notification publisher Core URL must be HTTPS")
        if not publisher_token.strip() or any(publisher_token == value for value in forbidden_credentials if value):
            raise ValueError("notification publisher requires a dedicated write credential")
        self._client = httpx.Client(base_url=base_url.rstrip("/"), timeout=5.0, transport=transport,
                                    headers={"Authorization": f"Bearer {publisher_token.strip()}"})

    def publish(self, payload: dict) -> bool:
        response = self._client.post("/api/phone-chat/notifications/publish", json=payload)
        response.raise_for_status()
        result = response.json()
        return bool(result.get("accepted"))

    def close(self):
        self._client.close()


class CoreNotificationPublisher:
    def __init__(self, client, *, clock=time.monotonic, retry_cooldown_seconds: float = 60.0):
        self.client = client
        self.clock = clock
        self.retry_cooldown_seconds = retry_cooldown_seconds
        self._queue: queue.Queue[dict] = queue.Queue(maxsize=16)
        self._lock = threading.Lock()
        self._pending: set[str] = set()
        self._published: dict[str, None] = {}
        self._last_attempt: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread = None

    def publish(self, intent) -> bool:
        payload = build_notification_payload(intent)
        if payload is None:
            return False
        key = payload["triggerKey"]
        now = self.clock()
        with self._lock:
            if key in self._pending or key in self._published or now - self._last_attempt.get(key, float("-inf")) < self.retry_cooldown_seconds:
                return False
            try:
                self._queue.put_nowait(payload)
            except queue.Full:
                return False
            self._pending.add(key)
            self._last_attempt[key] = now
            return True

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ascend-notification-publisher", daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                payload = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            key = payload["triggerKey"]
            accepted = False
            for attempt in range(3):
                try:
                    accepted = self.client.publish(payload)
                    break
                except Exception as error:
                    LOG.warning("Companion notification publish failed (%s)", type(error).__name__)
                    if attempt < 2 and not self._stop.wait(0.5 * (2 ** attempt)):
                        continue
                    break
            if accepted:
                with self._lock:
                    self._published[key] = None
                    if len(self._published) > 1_024:
                        self._published.pop(next(iter(self._published)))
            if not accepted:
                with self._lock:
                    self._last_attempt[key] = self.clock()
            with self._lock:
                self._pending.discard(key)
            self._queue.task_done()
        close = getattr(self.client, "close", None)
        if callable(close):
            close()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)


def build_notification_publisher(*, environ=None, transport=None):
    env = os.environ if environ is None else environ
    if env.get("ASCEND_NOTIFICATION_PUBLISHER_ENABLED", "").strip().lower() != "true":
        return None
    base_url = env.get("ASCEND_PHONE_CORE_URL", "").strip()
    token = env.get("ASCEND_NOTIFICATION_PUBLISHER_TOKEN", "").strip()
    if not base_url or not token:
        raise ValueError("notification publisher is enabled but Core URL or dedicated token is missing")
    forbidden = [env.get(name, "").strip() for name in (
        "ASCEND_NOTIFICATION_WORKER_TOKEN", "ASCEND_PHONE_WORKER_TOKEN", "ASCEND_CONTEXT_PUBLISHER_TOKEN",
        "ASCEND_STATUS_READ_CREDENTIAL", "ASCEND_DISCORD_BRIDGE_TOKEN", "CRON_SECRET",
    )]
    client = CoreNotificationPublisherClient(base_url, token, forbidden_credentials=forbidden, transport=transport)
    return CoreNotificationPublisher(client)
