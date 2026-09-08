import json
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError, URLError
import pytest

from integrations.ascend_client import AscendClient, AscendConnectionState


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_status_uses_configured_endpoint_and_integration_key():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(200, {"status": "ok"})

    result = AscendClient("http://ascend.local:8000", "secret", timeout_seconds=2.5,
                          opener=opener).get_status()

    assert result.state is AscendConnectionState.CONNECTED
    request, timeout = requests[0]
    assert request.full_url == "http://ascend.local:8000/api/integration/health"
    assert request.get_header("X-integration-key") == "secret"
    assert timeout == 2.5
    assert result.payload == {"status": "ok"}


def test_command_sends_explicit_user_text_and_response():
    requests = []

    def opener(request, timeout):
        requests.append(request)
        return FakeResponse(200, {"success": True, "message": "Workout logged."})

    timestamp = datetime(2026, 9, 6, 10, 30, tzinfo=timezone.utc)
    result = AscendClient("http://127.0.0.1:8000", opener=opener).send_command(
        "I finished my workout", source="watch", character_id="char-7", timestamp=timestamp, request_id="watch-1"
    )

    assert result.state is AscendConnectionState.CONNECTED
    assert result.message == "Workout logged."
    body = json.loads(requests[0].data.decode("utf-8"))
    assert body == {
        "source": "watch", "characterId": "char-7", "text": "I finished my workout",
        "timestamp": "2026-09-06T10:30:00+00:00", "requestId": "watch-1",
    }


def test_authentication_failure_has_clear_state():
    def opener(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, BytesIO(b'{"detail":"bad token"}'))

    result = AscendClient("http://127.0.0.1:8000", opener=opener).get_status()

    assert result.state is AscendConnectionState.AUTH_ERROR
    assert result.status_code == 401


def test_connection_failure_does_not_raise():
    def opener(request, timeout):
        raise URLError("connection refused")

    result = AscendClient("http://127.0.0.1:8000", opener=opener).get_status()

    assert result.state is AscendConnectionState.OFFLINE
    assert "connection refused" in result.error


def test_command_missing_character_id_fails_without_network_request():
    result = AscendClient("http://127.0.0.1:8000", opener=lambda *_: pytest.fail("network called")).send_command(
        "What missions do I have today?", source="phone", character_id=None
    )
    assert result.state is AscendConnectionState.CONFIG_ERROR


def test_command_uses_integration_key_and_generates_unique_request_ids():
    requests = []
    def opener(request, timeout):
        requests.append(request)
        return FakeResponse(200, {"success": True})
    client = AscendClient("http://127.0.0.1:8000", "key", opener=opener)
    first = client.send_command("What missions do I have today?", source="phone", character_id="char")
    second = client.send_command("What habits do I have today?", source="watch", character_id="char")
    bodies = [json.loads(request.data) for request in requests]
    assert first.state is second.state is AscendConnectionState.CONNECTED
    assert requests[0].get_header("X-integration-key") == "key"
    assert bodies[0]["requestId"] != bodies[1]["requestId"]
    assert bodies[0]["source"] == "phone" and bodies[1]["source"] == "watch"


def test_canonical_vision_command_source_is_supported_without_changing_device_authentication():
    requests = []
    def opener(request, timeout):
        requests.append(request)
        return FakeResponse(200, {"success": True})

    result = AscendClient("http://127.0.0.1:8000", "key", opener=opener).send_command(
        "Show my habits", source="ascend_vision", character_id="char")

    assert result.state is AscendConnectionState.CONNECTED
    assert json.loads(requests[0].data)["source"] == "ascend_vision"
    assert requests[0].get_header("X-integration-key") == "key"
