import pytest

from assistant.context_runtime import ContextRuntime
from integrations.context_publisher import CoreContextPublisher


class Provider:
    def read_snapshot(self):
        return ContextRuntime(device_id="laptop-1", boot_id="boot-1").read_snapshot()


class Client:
    def __init__(self):
        self.device_id = "laptop-1"
        self.published = []
        self.closed = False

    def publish(self, packet):
        self.published.append(packet)

    def close(self):
        self.closed = True


def test_context_publisher_sends_only_explicitly_shared_snapshot_and_closes_client():
    client = Client()
    publisher = CoreContextPublisher(Provider(), client, sharing_enabled=True)

    assert publisher.publish_once() is True
    assert len(client.published) == 1
    assert set(client.published[0]) == {
        "schemaVersion", "source", "deviceId", "bootId", "bootStartedAt", "snapshotRevision",
        "sourceSequences", "snapshotId", "generatedAt", "leaseExpiresAt", "paused", "fields",
    }
    publisher.close()
    assert client.closed is True


def test_context_publisher_disabled_does_not_read_or_send():
    class ForbiddenProvider:
        def read_snapshot(self):
            raise AssertionError("disabled sharing must not read context")

    client = Client()
    publisher = CoreContextPublisher(ForbiddenProvider(), client, sharing_enabled=False)

    assert publisher.publish_once() is False
    assert client.published == []


def test_context_client_rejects_reuse_of_other_credentials():
    from integrations.context_publisher import CoreContextPublisherClient

    with pytest.raises(ValueError, match="separate"):
        CoreContextPublisherClient(
            "https://core.example", "same", owner_id="owner-1", device_id="laptop-1",
            forbidden_credentials={"same"},
        )
