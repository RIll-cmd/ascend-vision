"""Outbound-only delivery of consented, privacy-minimal Core notifications."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import threading
from typing import Mapping
from urllib.parse import urlsplit

import httpx

LOG = logging.getLogger(__name__)
PUSH_HOSTS = ("fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com")
LOCK_SCREEN = {"title": "Ascend Vision", "body": "You have a private update."}


def _supported_endpoint(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 2_048:
        return False
    parts = urlsplit(value)
    host = (parts.hostname or "").lower().rstrip(".")
    return (parts.scheme == "https" and bool(host) and not parts.username and not parts.password
            and not parts.fragment and any(host == provider or host.endswith(f".{provider}") for provider in PUSH_HOSTS))


class NotificationJob:
    __slots__ = ("delivery_id", "intent_id", "owner_id", "channel", "destination", "expires_at", "attempt", "claim_id")

    def __init__(self, delivery_id, intent_id, owner_id, channel, destination, expires_at, attempt, claim_id):
        self.delivery_id, self.intent_id, self.owner_id = delivery_id, intent_id, owner_id
        self.channel, self.destination, self.expires_at = channel, destination, expires_at
        self.attempt, self.claim_id = attempt, claim_id

    @classmethod
    def parse(cls, payload: object, *, owner_id: str) -> "NotificationJob":
        fields = {"deliveryId", "intentId", "ownerId", "channel", "destinationId", "destination",
                  "expiresAt", "attempt", "claimId", "lockScreen"}
        if not isinstance(payload, dict) or set(payload) != fields:
            raise ValueError("notification job has an invalid shape")
        if payload["ownerId"] != owner_id:
            raise ValueError("notification job owner does not match this installation")
        if payload["channel"] not in {"pwa_push", "discord_dm"}:
            raise ValueError("notification job channel is unsupported")
        if payload["lockScreen"] != LOCK_SCREEN:
            raise ValueError("notification job lock-screen content is not generic")
        if not isinstance(payload["destination"], dict):
            raise ValueError("notification destination is invalid")
        destination = payload["destination"]
        if payload["channel"] == "pwa_push":
            if set(destination) != {"endpoint", "p256dh", "auth"} or not _supported_endpoint(destination.get("endpoint")):
                raise ValueError("notification push endpoint is not a supported browser provider")
            if any(not isinstance(destination[key], str) or not destination[key] for key in ("p256dh", "auth")):
                raise ValueError("notification push keys are invalid")
        elif (set(destination) != {"discordUserId"} or not isinstance(destination.get("discordUserId"), str)
              or not destination["discordUserId"].isdecimal() or not 17 <= len(destination["discordUserId"]) <= 20):
            raise ValueError("notification Discord destination is invalid")
        if any(not isinstance(payload[field], str) or not payload[field] for field in ("deliveryId", "intentId", "claimId")):
            raise ValueError("notification job identity is invalid")
        if type(payload["attempt"]) is not int or not 1 <= payload["attempt"] <= 3:
            raise ValueError("notification job attempt is invalid")
        try:
            expires = datetime.fromisoformat(str(payload["expiresAt"]).replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("notification job expiry is invalid") from error
        if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
            raise ValueError("notification job is expired")
        return cls(payload["deliveryId"], payload["intentId"], owner_id, payload["channel"], destination,
                   expires, payload["attempt"], payload["claimId"])


class CoreNotificationClient:
    def __init__(self, base_url: str, worker_token: str, *, timeout_seconds: float = 15.0, transport=None):
        parts = urlsplit(base_url.strip())
        loopback = {"localhost", "127.0.0.1", "::1"}
        if (parts.scheme not in {"https", "http"} or not parts.netloc or parts.username or parts.password
                or parts.query or parts.fragment or (parts.scheme != "https" and parts.hostname not in loopback)):
            raise ValueError("notification Core URL must be HTTPS except on loopback")
        if not worker_token.strip() or timeout_seconds <= 0:
            raise ValueError("notification worker credentials and timeout must be valid")
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport,
                                         headers={"Authorization": f"Bearer {worker_token.strip()}"})

    async def claim(self, channel: str = "pwa_push"):
        response = await self._client.post("/api/phone-chat/worker/notifications/claim", params={"channel": channel})
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    async def outcome(self, job: NotificationJob, result: str, *, retryable: bool = False,
                      revoke_destination: bool = False):
        response = await self._client.post(
            f"/api/phone-chat/worker/notifications/{job.delivery_id}/outcome",
            json={"claimId": job.claim_id, "outcome": result, "retryable": retryable,
                  "revokeDestination": revoke_destination},
        )
        response.raise_for_status()

    async def close(self):
        await self._client.aclose()


class PushProviderRejected(Exception):
    def __init__(self, *, retryable: bool, revoke_destination: bool = False):
        super().__init__("push provider rejected the request")
        self.retryable = retryable
        self.revoke_destination = revoke_destination


class WebPushTransport:
    def __init__(self, *, private_key: str, subject: str):
        if not private_key.strip() or not subject.startswith(("mailto:", "https://")):
            raise ValueError("VAPID private key and mailto/HTTPS subject are required")
        self.private_key = private_key
        self.subject = subject

    def send(self, destination: Mapping[str, str], payload: Mapping[str, str]):
        if set(payload) != {"intentId"}:
            raise ValueError("push payload must contain only an opaque intent ID")
        try:
            from pywebpush import WebPushException, webpush
            webpush(
                subscription_info={"endpoint": destination["endpoint"], "keys": {
                    "p256dh": destination["p256dh"], "auth": destination["auth"],
                }},
                data=json.dumps(dict(payload), separators=(",", ":")),
                vapid_private_key=self.private_key,
                vapid_claims={"sub": self.subject},
                ttl=3600,
            )
        except ImportError as error:
            raise RuntimeError("pywebpush is required to deliver PWA notifications") from error
        except Exception as error:
            if "WebPushException" in locals() and isinstance(error, WebPushException):
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status in {404, 410}:
                    raise PushProviderRejected(retryable=False, revoke_destination=True) from error
                if status == 429:
                    raise PushProviderRejected(retryable=True) from error
                if isinstance(status, int) and status >= 500:
                    raise RuntimeError("push provider result is unknown") from error
            # A network timeout may have been accepted remotely; never retry an ambiguous send.
            raise RuntimeError("push provider result is unknown") from error


class DiscordDmTransport:
    """Sends a generic DM only; private notification details stay in authenticated PWA."""
    def __init__(self, *, bot_token: str, client=None):
        if not bot_token.strip():
            raise ValueError("Discord bot token is required")
        self.client = client or httpx.Client(
            base_url="https://discord.com/api/v10", timeout=10.0, follow_redirects=False,
            headers={"Authorization": f"Bot {bot_token.strip()}", "Content-Type": "application/json"},
        )

    def send(self, destination: Mapping[str, str], payload: Mapping[str, str]):
        if set(payload) != {"content"}:
            raise ValueError("Discord notification payload must contain only generic content")
        channel_response = self.client.post("/users/@me/channels", json={"recipient_id": destination["discordUserId"]})
        if channel_response.status_code == 429:
            raise PushProviderRejected(retryable=True)
        if channel_response.status_code >= 500:
            raise RuntimeError("Discord DM creation result is unknown")
        if channel_response.status_code not in {200, 201}:
            raise PushProviderRejected(retryable=False)
        channel_id = channel_response.json().get("id")
        if not isinstance(channel_id, str) or not channel_id.isdecimal():
            raise RuntimeError("Discord DM channel response is unknown")
        message_response = self.client.post(f"/channels/{channel_id}/messages", json={
            "content": payload["content"], "allowed_mentions": {"parse": []},
        })
        if message_response.status_code == 429:
            raise PushProviderRejected(retryable=True)
        if message_response.status_code >= 500:
            raise RuntimeError("Discord message result is unknown")
        if message_response.status_code not in {200, 201}:
            raise PushProviderRejected(retryable=False)

    def close(self):
        self.client.close()


class NotificationDeliveryWorker:
    def __init__(self, client, push_transport, *, owner_id: str, channel: str = "pwa_push", poll_interval_seconds: float = 5.0):
        if not owner_id or channel not in {"pwa_push", "discord_dm"} or poll_interval_seconds <= 0:
            raise ValueError("notification worker owner and poll interval must be valid")
        self.client, self.push_transport, self.owner_id = client, push_transport, owner_id
        self.channel = channel
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = threading.Event()
        self._thread = None

    async def run_once(self) -> bool:
        payload = await self.client.claim(self.channel)
        if payload is None:
            return False
        job = payload if isinstance(payload, NotificationJob) else NotificationJob.parse(payload, owner_id=self.owner_id)
        if job.owner_id != self.owner_id:
            raise ValueError("notification job owner does not match this installation")
        try:
            # Private detail is intentionally not passed to either provider.
            data = ({"intentId": job.intent_id} if self.channel == "pwa_push" else {
                "content": "Ascend Vision: You have a private update. Open the signed-in app to view it.",
            })
            await asyncio.to_thread(self.push_transport.send, job.destination, data)
        except PushProviderRejected as error:
            await self._record_outcome(job, "failed", retryable=error.retryable,
                                       revoke_destination=error.revoke_destination)
        except Exception as error:
            LOG.warning("Notification provider result is unknown (%s)", type(error).__name__)
            await self._record_outcome(job, "unknown")
        else:
            # This means accepted by the browser push service, not received or read by a device.
            await self._record_outcome(job, "acknowledged")
        return True

    async def _record_outcome(self, job: NotificationJob, result: str, *, retryable: bool = False,
                              revoke_destination: bool = False) -> None:
        """Retry only Core outcome reporting; never resend the provider notification here."""
        last_error = None
        for attempt in range(3):
            try:
                if revoke_destination:
                    await self.client.outcome(job, result, retryable=retryable, revoke_destination=True)
                else:
                    await self.client.outcome(job, result, retryable=retryable)
                return
            except httpx.HTTPStatusError as error:
                status = error.response.status_code
                if status != 429 and status < 500:
                    raise
                last_error = error
            except httpx.TransportError as error:
                last_error = error
            if attempt < 2:
                await asyncio.sleep(0.25 * (2 ** attempt))
        if last_error is not None:
            raise last_error

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ascend-notification-delivery", daemon=True)
        self._thread.start()

    def _run(self):
        async def poll():
            try:
                while not self._stop.is_set():
                    try:
                        await self.run_once()
                    except Exception as error:
                        LOG.warning("Notification worker request failed (%s)", type(error).__name__)
                    await asyncio.to_thread(self._stop.wait, self.poll_interval_seconds)
            finally:
                await self.client.close()
        try:
            asyncio.run(poll())
        except Exception as error:
            LOG.warning("Notification worker stopped (%s)", type(error).__name__)

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        close = getattr(self.push_transport, "close", None)
        if callable(close):
            close()


def build_notification_worker(*, environ=None, transport=None):
    env = os.environ if environ is None else environ
    if env.get("ASCEND_NOTIFICATION_DELIVERY_ENABLED", "").strip().lower() != "true":
        return None
    enabled_channels = [
        channel for channel, key in (("pwa_push", "ASCEND_PWA_PUSH_DELIVERY_ENABLED"),
                                     ("discord_dm", "ASCEND_DISCORD_DM_DELIVERY_ENABLED"))
        if env.get(key, "").strip().lower() == "true"
    ]
    if not enabled_channels:
        return None
    required = ("ASCEND_PHONE_CORE_URL", "ASCEND_NOTIFICATION_WORKER_TOKEN", "ASCEND_PHONE_OWNER_ID")
    if any(not env.get(key, "").strip() for key in required):
        raise ValueError("notification delivery is enabled but Core credentials are missing")
    workers = []
    for channel in enabled_channels:
        client = CoreNotificationClient(env["ASCEND_PHONE_CORE_URL"], env["ASCEND_NOTIFICATION_WORKER_TOKEN"], transport=transport)
        if channel == "pwa_push":
            if not env.get("ASCEND_VAPID_PRIVATE_KEY", "").strip() or not env.get("ASCEND_VAPID_SUBJECT", "").strip():
                raise ValueError("PWA push delivery requires VAPID private key and subject")
            provider = WebPushTransport(private_key=env["ASCEND_VAPID_PRIVATE_KEY"], subject=env["ASCEND_VAPID_SUBJECT"])
        else:
            if not env.get("ASCEND_DISCORD_BOT_TOKEN", "").strip():
                raise ValueError("Discord DM delivery requires the bot token")
            provider = DiscordDmTransport(bot_token=env["ASCEND_DISCORD_BOT_TOKEN"])
        workers.append(NotificationDeliveryWorker(client, provider, owner_id=env["ASCEND_PHONE_OWNER_ID"].strip(), channel=channel))
    return NotificationWorkerGroup(workers)


class NotificationWorkerGroup:
    def __init__(self, workers):
        self.workers = tuple(workers)

    def start(self):
        for worker in self.workers:
            worker.start()

    def stop(self):
        for worker in reversed(self.workers):
            worker.stop()
