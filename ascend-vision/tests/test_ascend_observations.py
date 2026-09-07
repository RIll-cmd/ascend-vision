"""Phase 3A observation-contract tests."""
from datetime import datetime, timezone
from time import perf_counter, sleep
from unittest.mock import Mock
from uuid import UUID

from integrations.ascend_client import AscendClient, AscendConnectionState, AscendResult
from integrations.ascend_observations import (
    AscendObservationDispatcher,
    build_phone_usage_observation,
)
from state_machine import HoldEvent


class _Response:
    status = 200

    def read(self):
        return b'{"success": true, "duplicate": false}'

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _started_hold(*, posture="texting"):
    return HoldEvent(
        id=7,
        started_at=datetime(2026, 9, 7, 9, 30, tzinfo=timezone.utc),
        confidence=0.91,
        alert_allowed=True,
        posture=posture,
    )


def test_phone_usage_observation_uses_only_hold_transition_metadata():
    observation = build_phone_usage_observation(_started_hold())

    assert observation["source"] == "phone_cv"
    assert observation["type"] == "phone_usage_observed"
    assert observation["timestamp"] == "2026-09-07T09:30:00+00:00"
    assert observation["payload"] == {
        "confidence": 0.91,
        "posture": "texting",
        "state": "started",
    }
    UUID(observation["eventId"])
    assert not {"frame", "image", "video", "screenshot", "face_crop"} & observation["payload"].keys()


def test_phone_usage_observation_omits_unknown_posture():
    observation = build_phone_usage_observation(_started_hold(posture="none"))

    assert observation["payload"] == {"confidence": 0.91, "state": "started"}


def test_client_serializes_observation_character_and_durable_event_id():
    requests = []
    observation = build_phone_usage_observation(_started_hold())
    client = AscendClient("http://ascend.local", "integration-secret", opener=lambda request, timeout: (requests.append(request), _Response())[1])

    result = client.send_event(
        observation["type"], observation["payload"], source=observation["source"],
        timestamp=datetime.fromisoformat(observation["timestamp"]), character_id="char-7",
        event_id=observation["eventId"],
    )

    assert result.state is AscendConnectionState.CONNECTED
    body = __import__("json").loads(requests[0].data.decode("utf-8"))
    assert body == {**observation, "characterId": "char-7"}
    assert requests[0].get_header("X-integration-key") == "integration-secret"


def test_dispatcher_requires_a_configured_character_id():
    client = Mock()

    try:
        AscendObservationDispatcher(client, "")
    except ValueError as error:
        assert "character ID" in str(error)
    else:
        raise AssertionError("missing character ID must not start observation delivery")


def test_dispatcher_sends_one_transition_through_ascend_client_without_blocking():
    client = Mock()
    client.send_event.side_effect = lambda *args, **kwargs: (sleep(0.2), AscendResult(AscendConnectionState.CONNECTED))[1]
    dispatcher = AscendObservationDispatcher(client, "char-7", max_queue_size=1)
    try:
        started = perf_counter()
        accepted = dispatcher.submit_phone_usage(_started_hold())
        elapsed = perf_counter() - started
        assert accepted is True
        assert elapsed < 0.1
        dispatcher.flush_for_test()
        client.send_event.assert_called_once()
        args, kwargs = client.send_event.call_args
        assert args[0] == "phone_usage_observed"
        assert kwargs["source"] == "phone_cv"
        assert kwargs["character_id"] == "char-7"
        UUID(kwargs["event_id"])
        assert kwargs["timestamp"].tzinfo is not None
    finally:
        dispatcher.close()


def test_offline_delivery_is_contained_and_never_uses_tts():
    client = Mock()
    client.send_event.return_value = AscendResult(AscendConnectionState.OFFLINE, error="refused")
    dispatcher = AscendObservationDispatcher(client, "char-7")
    try:
        assert dispatcher.submit_phone_usage(_started_hold()) is True
        dispatcher.flush_for_test()
        client.send_event.assert_called_once()
    finally:
        dispatcher.close()
