"""Vision presence heartbeats stay independent from CV and voice work."""
import threading
import time


def test_heartbeat_worker_sends_immediately_and_stops_without_duplicates():
    from integrations.vision_heartbeat import VisionHeartbeatWorker

    sent = []
    first = threading.Event()

    class Client:
        def send_vision_heartbeat(self, **payload):
            sent.append(payload)
            first.set()
            return object()

    worker = VisionHeartbeatWorker(Client(), character_id='character-1', device_id='ascend-vision',
                                    version='1.0.0', interval_seconds=0.02)
    assert worker.start() is True
    assert first.wait(0.5)
    assert worker.start() is False
    worker.close()
    assert sent[0] == {
        'character_id': 'character-1',
        'device_id': 'ascend-vision',
        'version': '1.0.0',
    }


def test_heartbeat_failure_is_contained_and_does_not_retry_tightly():
    from integrations.vision_heartbeat import VisionHeartbeatWorker

    class Client:
        def __init__(self):
            self.calls = 0

        def send_vision_heartbeat(self, **_payload):
            self.calls += 1
            raise OSError('Core unavailable')

    client = Client()
    worker = VisionHeartbeatWorker(client, character_id='character-1', device_id='ascend-vision',
                                    version='1.0.0', interval_seconds=0.05)
    worker.start()
    time.sleep(0.12)
    worker.close()
    assert 1 <= client.calls <= 3


def test_close_waits_for_the_bounded_in_flight_heartbeat_to_finish():
    from integrations.vision_heartbeat import VisionHeartbeatWorker

    entered = threading.Event()
    release = threading.Event()

    class Client:
        def send_vision_heartbeat(self, **_payload):
            entered.set()
            release.wait()
            return object()

    worker = VisionHeartbeatWorker(Client(), character_id='character-1', device_id='ascend-vision',
                                    version='1.0.0', interval_seconds=10.0)
    assert worker.start() is True
    assert entered.wait(0.5)
    threading.Timer(1.1, release.set).start()
    worker.close()
    assert worker.is_running is False


def test_heartbeat_uses_resolved_authenticated_character():
    from datetime import datetime, timedelta, timezone

    from integrations.vision_context import VisionAuthContext, resolve_character_id
    from integrations.vision_heartbeat import VisionHeartbeatWorker
    from integrations.vision_token_store import VisionToken

    sent = []
    first = threading.Event()

    class Client:
        def send_vision_heartbeat(self, **payload):
            sent.append(payload)
            first.set()
            return object()

    expiry = datetime.now(timezone.utc) + timedelta(minutes=15)
    context = VisionAuthContext("guest-character", "Guest_d8d7", expiry)
    token = VisionToken("vision-token", expiry)
    worker = VisionHeartbeatWorker(
        Client(),
        character_id=resolve_character_id("stale-character", context, token),
        device_id="ascend-vision",
        version="1.0.0",
        interval_seconds=10.0,
    )
    worker.start()
    assert first.wait(0.5)
    worker.close()

    assert sent[0]["character_id"] == "guest-character"
