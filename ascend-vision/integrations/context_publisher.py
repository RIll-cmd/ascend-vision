"""Opt-in, outbound-only publisher for redacted current laptop context."""
from __future__ import annotations

import logging
import os
import threading
from urllib.parse import urlsplit

import httpx

from assistant.context_packet import build_context_packet


LOG = logging.getLogger(__name__)
_OTHER_CREDENTIALS = (
    "ASCEND_STATUS_READ_CREDENTIAL", "ASCEND_PHONE_WORKER_TOKEN", "ASCEND_DISCORD_BRIDGE_TOKEN",
)


class CoreContextPublisherClient:
    def __init__(self, base_url: str, token: str, *, owner_id: str, device_id: str,
                 forbidden_credentials=(), timeout_seconds: float = 5.0, transport=None):
        parts = urlsplit(base_url.strip())
        if (parts.scheme != "https" or not parts.netloc or parts.username or parts.password
                or parts.query or parts.fragment):
            raise ValueError("context Core URL must be HTTPS")
        if not token or not owner_id or not device_id:
            raise ValueError("context publisher credentials are not fully provisioned")
        if any(token == value for value in forbidden_credentials if value):
            raise ValueError("context publisher requires a separate write credential")
        self.owner_id = owner_id
        self.device_id = device_id
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )

    def publish(self, packet: dict) -> None:
        response = self._client.post("/api/phone-chat/context/publish", json=packet)
        response.raise_for_status()

    def close(self) -> None:
        self._client.close()


class CoreContextPublisher:
    def __init__(self, provider, client, *, sharing_enabled: bool,
                 interval_seconds: float = 5.0):
        if interval_seconds <= 0:
            raise ValueError("context publisher interval must be positive")
        self.provider = provider
        self.client = client
        self.sharing_enabled = bool(sharing_enabled)
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def publish_once(self) -> bool:
        if not self.sharing_enabled:
            return False
        snapshot = self.provider.read_snapshot()
        if snapshot.device_id != self.client.device_id:
            raise ValueError("context snapshot device does not match publisher credentials")
        packet = build_context_packet(snapshot)
        self.client.publish(packet)
        return True

    def start(self) -> None:
        if not self.sharing_enabled:
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ascend-context-publisher", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.publish_once()
            except Exception as error:
                LOG.warning("Laptop context publication failed (%s)", type(error).__name__)
            self._stop.wait(self.interval_seconds)

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


def build_context_publisher(provider, *, sharing_enabled: bool, environ=None):
    if not sharing_enabled:
        return None
    env = os.environ if environ is None else environ
    token = env.get("ASCEND_CONTEXT_PUBLISHER_TOKEN", "").strip()
    owner_id = env.get("ASCEND_CONTEXT_OWNER_ID", "").strip()
    device_id = env.get("ASCEND_DEVICE_ID", "ascend-vision-desktop").strip()
    base_url = (env.get("ASCEND_CORE_BASE_URL") or env.get("ASCEND_BASE_URL", "")).strip()
    forbidden = [env.get(name, "").strip() for name in _OTHER_CREDENTIALS]
    client = CoreContextPublisherClient(base_url, token, owner_id=owner_id,
                                        device_id=device_id, forbidden_credentials=forbidden)
    return CoreContextPublisher(provider, client, sharing_enabled=True)
