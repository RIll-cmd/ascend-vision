from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from integrations.notification_publisher import (
    CoreNotificationPublisher,
    CoreNotificationPublisherClient,
    build_notification_payload,
    build_notification_publisher,
)


def intent(rule_id="agent_needs_input"):
    return SimpleNamespace(
        rule_id=rule_id,
        deduplication_key="agent_needs_input:codex:opaque-operation-ref",
        message="Sensitive label should not leave the laptop.",
    )


def test_agent_input_publisher_emits_only_generic_bounded_payload():
    payload = build_notification_payload(intent(), now=datetime(2026, 9, 28, tzinfo=timezone.utc))
    assert payload["category"] == "Agent attention"
    assert "Sensitive label" not in str(payload)
    assert payload["detail"] == "A connected AI agent requested your input. Open Ascend Vision to check its latest status."
    assert payload["triggerKey"].startswith("agent-input:")
    assert len(payload["triggerKey"]) <= 128
    assert build_notification_payload(intent("break_suggestion")) is None


def test_publisher_client_uses_dedicated_credential_and_core_idempotency_key():
    def handle(request):
        assert request.url.path == "/api/phone-chat/notifications/publish"
        assert request.headers["authorization"] == "Bearer dedicated-publisher"
        return httpx.Response(202, json={"accepted": True})

    transport = httpx.MockTransport(handle)
    client = CoreNotificationPublisherClient("https://core.example", "dedicated-publisher", transport=transport)
    assert client.publish(build_notification_payload(intent())) is True
    client.close()
    with pytest.raises(ValueError, match="dedicated"):
        CoreNotificationPublisherClient("https://core.example", "same-as-worker",
                                        forbidden_credentials=["same-as-worker"])


def test_remote_notification_publisher_is_off_by_default_and_dedupes_queued_events():
    assert build_notification_publisher(environ={}) is None

    class Client:
        def publish(self, payload): return True

    publisher = CoreNotificationPublisher(Client(), clock=lambda: 100.0)
    assert publisher.publish(intent()) is True
    assert publisher.publish(intent()) is False
    assert publisher._queue.qsize() == 1
    publisher.close()
