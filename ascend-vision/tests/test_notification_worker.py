import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from integrations.notification_worker import (
    DiscordDmTransport,
    NotificationDeliveryWorker,
    NotificationJob,
    PushProviderRejected,
)


def job(**overrides):
    value = {
        "deliveryId": "delivery-1", "intentId": "intent-1", "ownerId": "owner-1",
        "channel": "pwa_push", "destinationId": "device-1",
        "destination": {"endpoint": "https://fcm.googleapis.com/capability", "p256dh": "public", "auth": "secret"},
        "expiresAt": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
        "attempt": 1, "claimId": "claim-1",
        "lockScreen": {"title": "Ascend Vision", "body": "You have a private update."},
    }
    value.update(overrides)
    return value


def test_notification_job_rejects_wrong_owner_and_remote_details_on_lock_screen():
    import pytest

    with pytest.raises(ValueError, match="owner"):
        NotificationJob.parse(job(), owner_id="other-owner")
    with pytest.raises(ValueError, match="shape"):
        NotificationJob.parse(job(detail="private worker payload"), owner_id="owner-1")
    with pytest.raises(ValueError, match="generic"):
        NotificationJob.parse(job(lockScreen={"title": "private detail", "body": "private detail"}), owner_id="owner-1")


def test_worker_sends_only_opaque_intent_id_and_records_provider_acceptance():
    class Client:
        async def claim(self, channel): return NotificationJob.parse(job(), owner_id="owner-1")
        async def outcome(self, current, result, *, retryable=False): self.result = (result, retryable)

    class Push:
        def send(self, destination, payload): self.value = (destination, payload)

    client, push = Client(), Push()
    worker = NotificationDeliveryWorker(client, push, owner_id="owner-1")
    assert asyncio.run(worker.run_once()) is True
    assert push.value[1] == {"intentId": "intent-1"}
    assert "private detail" not in str(push.value[1])
    assert client.result == ("acknowledged", False)


def test_worker_retries_transient_core_outcome_write_without_resending_provider_message():
    import httpx

    class Client:
        def __init__(self): self.outcomes = 0
        async def claim(self, channel): return NotificationJob.parse(job(), owner_id="owner-1")
        async def outcome(self, current, result, *, retryable=False, revoke_destination=False):
            self.outcomes += 1
            if self.outcomes == 1:
                request = httpx.Request("POST", "https://core.test/outcome")
                raise httpx.ReadTimeout("temporary timeout", request=request)

    class Push:
        def __init__(self): self.calls = 0
        def send(self, destination, payload): self.calls += 1

    client, push = Client(), Push()
    worker = NotificationDeliveryWorker(client, push, owner_id="owner-1")
    assert asyncio.run(worker.run_once()) is True
    assert client.outcomes == 2
    assert push.calls == 1


def test_worker_records_retryable_known_rejection_but_unknown_transport_result_once():
    class Client:
        async def claim(self, channel): return NotificationJob.parse(job(), owner_id="owner-1")
        async def outcome(self, current, result, *, retryable=False): self.result = (result, retryable)

    class Push:
        def send(self, destination, payload): raise RuntimeError("network timeout")

    client = Client()
    assert asyncio.run(NotificationDeliveryWorker(client, Push(), owner_id="owner-1").run_once()) is True
    assert client.result == ("unknown", False)


def test_discord_worker_sends_generic_content_only():
    discord_job = job(channel="discord_dm", destination={"discordUserId": "123456789012345678"})

    class Client:
        async def claim(self, channel):
            assert channel == "discord_dm"
            return NotificationJob.parse(discord_job, owner_id="owner-1")
        async def outcome(self, current, result, *, retryable=False): self.result = result

    class Discord:
        def send(self, destination, payload): self.value = (destination, payload)

    client, provider = Client(), Discord()
    worker = NotificationDeliveryWorker(client, provider, owner_id="owner-1", channel="discord_dm")
    assert asyncio.run(worker.run_once()) is True
    assert provider.value[0] == {"discordUserId": "123456789012345678"}
    assert provider.value[1] == {"content": "Ascend Vision: You have a private update. Open the signed-in app to view it."}
    assert client.result == "acknowledged"


def test_notification_delivery_is_disabled_by_default_and_channels_are_separately_configured():
    from integrations.notification_worker import build_notification_worker

    assert build_notification_worker(environ={}) is None
    with pytest.raises(ValueError, match="Core credentials"):
        build_notification_worker(environ={"ASCEND_NOTIFICATION_DELIVERY_ENABLED": "true",
                                           "ASCEND_PWA_PUSH_DELIVERY_ENABLED": "true"})


def test_discord_transport_posts_generic_dm_without_mentions():
    class Response:
        status_code = 200
        def __init__(self, value): self.value = value
        def json(self): return self.value

    class Http:
        def __init__(self): self.requests = []
        def post(self, path, *, json):
            self.requests.append((path, json))
            return Response({"id": "223456789012345678"} if path == "/users/@me/channels" else {"id": "323456789012345678"})
        def close(self): pass

    http = Http()
    transport = DiscordDmTransport(bot_token="not-logged", client=http)
    transport.send({"discordUserId": "123456789012345678"}, {
        "content": "Ascend Vision: You have a private update. Open the signed-in app to view it.",
    })
    assert http.requests[0] == ("/users/@me/channels", {"recipient_id": "123456789012345678"})
    assert http.requests[1][1]["allowed_mentions"] == {"parse": []}
    assert "private detail" not in str(http.requests)


def test_discord_dm_rate_limit_is_retryable_but_server_error_is_not_replayed():
    class Response:
        def __init__(self, status): self.status_code = status

    class Http:
        def __init__(self, status): self.status = status
        def post(self, path, *, json): return Response(self.status)
        def close(self): pass

    destination = {"discordUserId": "123456789012345678"}
    payload = {"content": "Ascend Vision: You have a private update."}
    with pytest.raises(PushProviderRejected) as limited:
        DiscordDmTransport(bot_token="token", client=Http(429)).send(destination, payload)
    assert limited.value.retryable is True
    with pytest.raises(RuntimeError, match="unknown"):
        DiscordDmTransport(bot_token="token", client=Http(503)).send(destination, payload)


def test_worker_revokes_permanently_expired_push_destination():
    class Client:
        async def claim(self, channel): return NotificationJob.parse(job(), owner_id="owner-1")
        async def outcome(self, current, result, *, retryable=False, revoke_destination=False):
            self.result = (result, retryable, revoke_destination)

    class Push:
        def send(self, destination, payload):
            raise PushProviderRejected(retryable=False, revoke_destination=True)

    client = Client()
    assert asyncio.run(NotificationDeliveryWorker(client, Push(), owner_id="owner-1").run_once()) is True
    assert client.result == ("failed", False, True)
