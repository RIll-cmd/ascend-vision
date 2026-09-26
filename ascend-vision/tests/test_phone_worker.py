import asyncio
import logging
import threading
from datetime import datetime, timedelta, timezone

import httpx
import pytest


def _job(**updates):
    value = {
        "messageId": "4bfb3bb8-8ee7-4acd-8f17-cf98119c20fd",
        "ownerId": "owner-1",
        "deviceId": "device-1",
        "sessionId": "tab-1",
        "text": "What is Ascend Hub status?",
        "expiresAt": "2026-09-27T12:00:00Z",
        "attempt": 1,
        "leaseId": "lease_1",
        "leaseExpiresAt": "2026-09-26T12:01:00Z",
    }
    return {**value, **updates}


class QueueClient:
    def __init__(self, job=None, *, start_ok=True, renew_ok=True):
        self.job = job
        self.start_ok = start_ok
        self.renew_ok = renew_ok
        self.calls = []
        self.claimed = threading.Event()
        self.closed = threading.Event()

    async def claim(self):
        self.calls.append(("claim",))
        self.claimed.set()
        return self.job

    async def start(self, job):
        self.calls.append(("start", job.message_id, job.lease_id))
        return self.start_ok

    async def renew(self, job):
        self.calls.append(("renew", job.message_id, job.lease_id))
        return self.renew_ok

    async def complete(self, job, result):
        self.calls.append(("complete", job.message_id, dict(result)))

    async def close(self):
        self.closed.set()


class Handler:
    def __init__(self, reply="verified answer", error=None):
        self.calls = []
        self.reply = reply
        self.error = error
        self.expired_sessions = 0

    def expire_sessions(self):
        self.expired_sessions += 1

    def handle(self, owner_id, channel, session_id, text):
        self.calls.append((owner_id, channel, session_id, text))
        if self.error:
            raise self.error
        from assistant.service import AssistantReply
        return AssistantReply(self.reply, "model")


def test_core_phone_client_uses_worker_bearer_and_strict_claim_contract():
    from integrations.phone_worker import CorePhoneClient

    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=_job())

    async def scenario():
        client = CorePhoneClient("https://core.example", "worker-secret", owner_id="owner-1", transport=httpx.MockTransport(respond))
        job = await client.claim()
        assert job.owner_id == "owner-1"
        assert seen[0].url.path == "/api/phone-chat/worker/claim"
        assert seen[0].headers["Authorization"] == "Bearer worker-secret"
        assert b"What is Ascend Hub status?" not in seen[0].content
        await client.close()

    asyncio.run(scenario())


def test_core_phone_client_returns_none_for_no_work_and_fails_closed_on_extra_fields():
    from integrations.phone_worker import CorePhoneClient

    async def scenario():
        empty = CorePhoneClient("https://core.example", "secret", owner_id="owner-1", transport=httpx.MockTransport(
            lambda request: httpx.Response(204),
        ))
        assert await empty.claim() is None
        await empty.close()

        malformed = CorePhoneClient("https://core.example", "secret", owner_id="owner-1", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=_job(ownerId="owner-1", arbitrary="data")),
        ))
        with pytest.raises(ValueError, match="invalid"):
            await malformed.claim()
        await malformed.close()

    asyncio.run(scenario())


def test_worker_skips_empty_queue_expired_jobs_and_stale_leases():
    from integrations.phone_worker import PhoneQueueWorker

    async def scenario():
        now = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)
        empty_handler = Handler()
        empty = PhoneQueueWorker(QueueClient(), empty_handler, owner_id="owner-1", clock=lambda: now)
        assert await empty.run_once() is False
        assert empty_handler.calls == []
        assert empty_handler.expired_sessions == 1

        expired_handler = Handler()
        expired_client = QueueClient(_job(expiresAt="2026-09-26T11:59:00Z"), start_ok=False)
        expired = PhoneQueueWorker(expired_client, expired_handler, owner_id="owner-1", clock=lambda: now)
        assert await expired.run_once() is True
        assert expired_handler.calls == []
        assert [call[0] for call in expired_client.calls] == ["claim", "start"]

        stale_handler = Handler()
        stale_client = QueueClient(_job(), start_ok=False)
        stale = PhoneQueueWorker(stale_client, stale_handler, owner_id="owner-1", clock=lambda: now)
        assert await stale.run_once() is False
        assert stale_handler.calls == []

    asyncio.run(scenario())


def test_worker_renews_lease_routes_isolated_phone_session_and_completes_once():
    from integrations.phone_worker import PhoneQueueWorker

    async def scenario():
        client = QueueClient(_job())
        handler = Handler()
        worker = PhoneQueueWorker(client, handler, owner_id="owner-1", clock=lambda: datetime(2026, 9, 26, 12, tzinfo=timezone.utc))
        assert await worker.run_once() is True
        assert handler.calls == [("owner-1", "phone_pwa", "tab-1", "What is Ascend Hub status?")]
        assert handler.expired_sessions == 1
        assert [call[0] for call in client.calls] == ["claim", "start", "renew", "complete"]
        assert client.calls[-1][2] == {"status": "completed", "reply": "verified answer"}

    asyncio.run(scenario())


def test_worker_fails_generically_without_logging_prompt_reply_or_exception(caplog):
    from integrations.phone_worker import PhoneQueueWorker

    async def scenario():
        client = QueueClient(_job())
        handler = Handler(error=RuntimeError("secret prompt and provider response"))
        worker = PhoneQueueWorker(client, handler, owner_id="owner-1")
        with caplog.at_level(logging.WARNING):
            assert await worker.run_once() is True
        assert client.calls[-1][2] == {"status": "failed", "errorCode": "assistant_unavailable"}
        assert "What is Ascend Hub status?" not in caplog.text
        assert "secret prompt" not in caplog.text

    asyncio.run(scenario())


def test_worker_does_not_submit_generation_if_message_expires_while_it_runs():
    from integrations.phone_worker import PhoneQueueWorker

    async def scenario():
        times = iter([
            datetime(2026, 9, 26, 12, tzinfo=timezone.utc),
            datetime(2026, 9, 28, 12, tzinfo=timezone.utc),
        ])
        client = QueueClient(_job())
        worker = PhoneQueueWorker(client, Handler(), owner_id="owner-1", clock=lambda: next(times))
        assert await worker.run_once() is True
        assert client.calls[-1][2] == {"status": "failed", "errorCode": "job_expired"}

    asyncio.run(scenario())


def test_worker_rejects_owner_mismatch_and_invalid_job_before_assistant_call():
    from integrations.phone_worker import PhoneQueueWorker

    async def scenario():
        handler = Handler()
        client = QueueClient(_job(ownerId="other-owner"))
        worker = PhoneQueueWorker(client, handler, owner_id="owner-1")
        with pytest.raises(ValueError, match="owner"):
            await worker.run_once()
        assert handler.calls == []

        bad_handler = Handler()
        malformed = QueueClient(_job(attempt=4))
        with pytest.raises(ValueError):
            await PhoneQueueWorker(malformed, bad_handler, owner_id="owner-1").run_once()
        assert bad_handler.calls == []

    asyncio.run(scenario())


def test_phone_worker_configuration_is_disabled_by_default_and_rejects_partial_credentials(monkeypatch):
    from config import PhoneChatConfig
    from integrations.phone_worker import build_phone_worker

    config = PhoneChatConfig()
    assert build_phone_worker(config, Handler(), environ={}) is None
    enabled = PhoneChatConfig(enabled=True)
    with pytest.raises(ValueError, match="provisioned"):
        build_phone_worker(enabled, Handler(), environ={"ASCEND_PHONE_WORKER_TOKEN": "secret"})

    monkeypatch.setenv("ASCEND_PHONE_CORE_URL", "https://core.example")
    monkeypatch.setenv("ASCEND_PHONE_WORKER_TOKEN", "secret")
    monkeypatch.setenv("ASCEND_PHONE_OWNER_ID", "owner-1")
    worker = build_phone_worker(enabled, Handler())
    assert worker.owner_id == "owner-1"
    assert worker.poll_interval_seconds == config.poll_interval_seconds


def test_worker_start_stop_closes_the_outbound_client():
    from integrations.phone_worker import PhoneQueueWorker

    client = QueueClient()
    worker = PhoneQueueWorker(client, Handler(), owner_id="owner-1", poll_interval_seconds=0.1)
    worker.start()
    assert client.claimed.wait(1)
    worker.stop()

    assert client.closed.is_set()
    assert client.calls[0] == ("claim",)
