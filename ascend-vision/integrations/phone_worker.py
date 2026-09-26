"""Outbound-only Core queue client and single-owner phone-chat worker."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
import re
import threading
from typing import Mapping
from urllib.parse import urlsplit
from uuid import UUID

import httpx

LOG = logging.getLogger(__name__)
_JOB_FIELDS = frozenset({
    "messageId", "ownerId", "deviceId", "sessionId", "text", "expiresAt",
    "attempt", "leaseId", "leaseExpiresAt",
})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _timestamp(value, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"phone worker job is invalid: {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"phone worker job is invalid: {field}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"phone worker job is invalid: {field}")
    return parsed


@dataclass(frozen=True)
class WorkerJob:
    message_id: str
    owner_id: str
    device_id: str
    session_id: str
    text: str
    expires_at: datetime
    attempt: int
    lease_id: str
    lease_expires_at: datetime

    @classmethod
    def parse(cls, payload: object, *, owner_id: str) -> "WorkerJob":
        if not isinstance(payload, dict) or frozenset(payload) != _JOB_FIELDS:
            raise ValueError("phone worker job is invalid: fields")
        try:
            message_id = str(UUID(payload["messageId"]))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("phone worker job is invalid: messageId") from error
        actual_owner = payload["ownerId"]
        if not isinstance(actual_owner, str) or not actual_owner or actual_owner != owner_id:
            raise ValueError("phone worker job owner does not match this installation")
        for key in ("deviceId", "sessionId", "leaseId"):
            if not isinstance(payload[key], str) or not _IDENTIFIER.fullmatch(payload[key]):
                raise ValueError(f"phone worker job is invalid: {key}")
        text = payload["text"]
        if not isinstance(text, str) or not text.strip() or len(text) > 4_000:
            raise ValueError("phone worker job is invalid: text")
        attempt = payload["attempt"]
        if type(attempt) is not int or attempt < 1 or attempt > 3:
            raise ValueError("phone worker job is invalid: attempt")
        return cls(
            message_id=message_id,
            owner_id=actual_owner,
            device_id=payload["deviceId"],
            session_id=payload["sessionId"],
            text=text,
            expires_at=_timestamp(payload["expiresAt"], "expiresAt"),
            attempt=attempt,
            lease_id=payload["leaseId"],
            lease_expires_at=_timestamp(payload["leaseExpiresAt"], "leaseExpiresAt"),
        )


class CorePhoneClient:
    """Scoped client for only the phone worker's claim/start/renew/complete routes."""

    def __init__(self, base_url: str, worker_token: str, *, owner_id: str,
                 timeout_seconds: float = 15.0, transport=None):
        parts = urlsplit(base_url.strip())
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if (parts.scheme not in {"https", "http"} or not parts.netloc or parts.username or parts.password
                or parts.query or parts.fragment or (parts.scheme != "https" and parts.hostname not in local_hosts)):
            raise ValueError("phone Core URL must be HTTPS (HTTP is allowed only for loopback development)")
        if not isinstance(worker_token, str) or not worker_token.strip():
            raise ValueError("phone worker token must be nonempty")
        if not isinstance(owner_id, str) or not owner_id.strip():
            raise ValueError("phone worker owner ID must be nonempty")
        if type(timeout_seconds) not in (int, float) or timeout_seconds <= 0:
            raise ValueError("phone worker timeout must be positive")
        self.owner_id = owner_id.strip()
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport,
            headers={"Authorization": f"Bearer {worker_token.strip()}"},
        )

    async def claim(self) -> WorkerJob | None:
        response = await self._client.post("/api/phone-chat/worker/claim")
        if response.status_code == 204:
            return None
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise ValueError("phone worker claim response is invalid") from error
        return WorkerJob.parse(payload, owner_id=self.owner_id)

    async def start(self, job: WorkerJob) -> bool:
        return await self._lease_post(job, "start")

    async def renew(self, job: WorkerJob) -> bool:
        return await self._lease_post(job, "renew")

    async def _lease_post(self, job: WorkerJob, action: str) -> bool:
        response = await self._client.post(
            f"/api/phone-chat/worker/jobs/{job.message_id}/{action}",
            headers={"X-Phone-Lease": job.lease_id},
        )
        if response.status_code in {404, 409}:
            return False
        response.raise_for_status()
        return response.status_code == 204

    async def complete(self, job: WorkerJob, result: Mapping[str, str]) -> None:
        response = await self._client.post(
            f"/api/phone-chat/worker/jobs/{job.message_id}/complete",
            headers={"X-Phone-Lease": job.lease_id}, json=dict(result),
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()


class PhoneQueueWorker:
    """Polls Core outbound and runs phone turns without exposing an inbound port."""

    def __init__(self, client, handler, *, owner_id: str, poll_interval_seconds: float = 2.0,
                 lease_renew_interval_seconds: float = 20.0,
                 shutdown_timeout_seconds: float = 3.0, clock=lambda: datetime.now(timezone.utc)):
        if not owner_id:
            raise ValueError("owner_id must be nonempty")
        if poll_interval_seconds <= 0 or lease_renew_interval_seconds <= 0 or shutdown_timeout_seconds <= 0:
            raise ValueError("phone worker intervals must be positive")
        self.client = client
        self.handler = handler
        self.owner_id = owner_id
        self.poll_interval_seconds = poll_interval_seconds
        self.lease_renew_interval_seconds = lease_renew_interval_seconds
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    async def run_once(self) -> bool:
        expire_sessions = getattr(self.handler, "expire_sessions", None)
        if callable(expire_sessions):
            await asyncio.to_thread(expire_sessions)
        job = await self.client.claim()
        if job is None:
            return False
        if not isinstance(job, WorkerJob):
            job = WorkerJob.parse(job, owner_id=self.owner_id)
        if job.owner_id != self.owner_id:
            raise ValueError("phone worker job owner does not match this installation")
        if job.expires_at <= self._clock():
            # Core's start endpoint atomically expires this claimed item and
            # erases its prompt; no model call or completion is needed.
            await self.client.start(job)
            return True
        if not await self.client.start(job):
            return False
        # Renew immediately before generation so time spent waiting on an LLM
        # cannot consume most of the initial 60-second lease.
        if not await self.client.renew(job):
            return False

        stopped = asyncio.Event()
        lease_lost = asyncio.Event()

        async def renew_while_generating():
            while not stopped.is_set():
                try:
                    await asyncio.wait_for(stopped.wait(), timeout=self.lease_renew_interval_seconds)
                    return
                except asyncio.TimeoutError:
                    try:
                        if not await self.client.renew(job):
                            lease_lost.set()
                            return
                    except Exception:
                        lease_lost.set()
                        return

        renewal = asyncio.create_task(renew_while_generating())
        try:
            reply = await asyncio.to_thread(
                self.handler.handle, job.owner_id, "phone_pwa", job.session_id, job.text,
            )
            text = getattr(reply, "text", None)
            if not isinstance(text, str) or not text.strip() or len(text) > 8_000:
                result = {"status": "failed", "errorCode": "assistant_unavailable"}
            else:
                result = {"status": "completed", "reply": text.strip()}
        except Exception as error:
            # Exception messages can contain prompts or provider response data.
            LOG.warning("Phone assistant job failed (%s)", type(error).__name__)
            result = {"status": "failed", "errorCode": "assistant_unavailable"}
        finally:
            stopped.set()
            await renewal

        if lease_lost.is_set():
            return False
        if job.expires_at <= self._clock():
            result = {"status": "failed", "errorCode": "job_expired"}
        await self.client.complete(job, result)
        return True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="ascend-phone-chat-worker", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            asyncio.run(self._poll())
        except Exception as error:
            LOG.warning("Phone worker stopped (%s)", type(error).__name__)

    async def _poll(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await self.run_once()
                except Exception as error:
                    LOG.warning("Phone worker request failed (%s)", type(error).__name__)
                await asyncio.to_thread(self._stop.wait, self.poll_interval_seconds)
        finally:
            close = getattr(self.client, "close", None)
            if callable(close):
                await close()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.shutdown_timeout_seconds)
            if self._thread.is_alive():
                LOG.warning("Phone worker did not stop within the configured timeout")


def build_phone_worker(config, handler, *, environ=None, transport=None):
    """Create only when explicitly enabled and all scoped credentials are present."""
    env = os.environ if environ is None else environ
    if not config.enabled:
        return None
    base_url = env.get(config.core_url_env, "").strip()
    token = env.get(config.worker_token_env, "").strip()
    owner_id = env.get(config.owner_id_env, "").strip()
    if not base_url or not token or not owner_id:
        raise ValueError("phone chat is enabled but its Core URL, worker token, and owner ID are not fully provisioned")
    client = CorePhoneClient(
        base_url, token, owner_id=owner_id,
        timeout_seconds=config.request_timeout_seconds, transport=transport,
    )
    return PhoneQueueWorker(
        client, handler, owner_id=owner_id,
        poll_interval_seconds=config.poll_interval_seconds,
        lease_renew_interval_seconds=config.lease_renew_interval_seconds,
        shutdown_timeout_seconds=config.shutdown_timeout_seconds,
    )
