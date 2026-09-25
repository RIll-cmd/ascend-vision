import io
import json
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError

import pytest

from integrations.status_shelf import StatusShelfError, StatusShelfReader


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


class FakeResponse:
    status = 200

    def __init__(self, payload):
        self._body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self._body.close()

    def read(self, amount=-1):
        return self._body.read(amount)


def shelf(*services, generated_at=NOW):
    return {
        "schemaVersion": 1,
        "generatedAt": generated_at.isoformat(),
        "services": list(services),
    }


def agent(*, state="working", heartbeat=NOW, service_id="codex-cli", instance_id="desktop"):
    return {
        "serviceId": service_id,
        "instanceId": instance_id,
        "serviceType": "agent",
        "state": state,
        "stateSince": NOW.isoformat(),
        "lastHeartbeatAt": heartbeat.isoformat() if heartbeat else None,
        "staleAfterSeconds": 30,
        "activity": {"kind": "operation", "label": "run tests"} if state == "working" else None,
    }


def test_reader_uses_dedicated_read_credential_and_parses_fresh_shelf():
    captured = {}

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(shelf(agent()))

    snapshot = StatusShelfReader(
        "https://core.local:8000/", "reader-id.reader-secret", opener=opener,
        timeout_seconds=2.0,
    ).read(now=NOW)

    request = captured["request"]
    assert request.get_method() == "GET"
    assert request.full_url == "https://core.local:8000/api/status/shelf"
    assert request.get_header("X-status-read-credential") == "reader-id.reader-secret"
    assert request.get_header("X-integration-key") is None
    assert request.get_header("Authorization") is None
    assert request.get_header("Cache-control") == "no-store"
    assert captured["timeout"] == 2.0
    assert snapshot.services[0].state == "working"
    assert snapshot.services[0].service_id == "codex-cli"


def test_reader_requires_a_dedicated_credential_before_network_call():
    def opener(*_args, **_kwargs):
        raise AssertionError("network must not be used")

    with pytest.raises(StatusShelfError):
        StatusShelfReader("https://core.local", None, opener=opener).read(now=NOW)


def test_reader_rejects_remote_plain_http_and_embedded_url_credentials():
    with pytest.raises(ValueError):
        StatusShelfReader("http://core.example", "reader.secret")
    with pytest.raises(ValueError):
        StatusShelfReader("https://user:pass@core.example", "reader.secret")


@pytest.mark.parametrize("payload", [
    {"schemaVersion": 2, "generatedAt": NOW.isoformat(), "services": []},
    {"schemaVersion": 1, "generatedAt": "yesterday", "services": []},
    {"schemaVersion": 1, "generatedAt": NOW.isoformat(), "services": "wrong"},
    shelf(agent(state="confused")),
    shelf(agent(service_id="<script>")),
    shelf(agent(heartbeat=None, state="working")),
])
def test_reader_rejects_malformed_or_contradictory_shelf(payload):
    reader = StatusShelfReader("https://core.local", "reader.secret", opener=lambda *_args, **_kwargs: FakeResponse(payload))

    with pytest.raises(StatusShelfError):
        reader.read(now=NOW)


def test_reader_rejects_old_snapshot_and_treats_stale_heartbeat_as_offline():
    old = StatusShelfReader(
        "https://core.local", "reader.secret",
        opener=lambda *_args, **_kwargs: FakeResponse(shelf(generated_at=NOW-timedelta(minutes=5))),
    )
    with pytest.raises(StatusShelfError):
        old.read(now=NOW)

    stale = StatusShelfReader(
        "https://core.local", "reader.secret",
        opener=lambda *_args, **_kwargs: FakeResponse(shelf(agent(heartbeat=NOW-timedelta(seconds=31)))),
    ).read(now=NOW)
    assert stale.services[0].state == "offline"


def test_reader_rejects_unauthorized_network_and_oversized_responses():
    def unauthorized(*_args, **_kwargs):
        raise HTTPError("https://core.local/api/status/shelf", 401, "denied", {}, io.BytesIO(b"{}"))

    for opener in (
        unauthorized,
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")),
        lambda *_args, **_kwargs: FakeResponse(shelf() | {"padding": "x" * 300_000}),
    ):
        with pytest.raises(StatusShelfError):
            StatusShelfReader("https://core.local", "reader.secret", opener=opener).read(now=NOW)
