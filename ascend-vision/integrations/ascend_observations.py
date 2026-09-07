"""Translation and non-blocking delivery for observation-only Ascend events."""
from __future__ import annotations

from datetime import datetime
import logging
from queue import Full, Queue
from threading import Thread
from typing import Any
from uuid import uuid4

from integrations.ascend_client import AscendClient, AscendConnectionState
from state_machine import HoldEvent


LOG = logging.getLogger(__name__)
_STOP = object()


def build_phone_usage_observation(event: HoldEvent) -> dict[str, Any]:
    """Build semantic telemetry from a confirmed hold transition, never camera data."""
    if event.started_at.tzinfo is None or event.started_at.utcoffset() is None:
        raise ValueError("Hold observation timestamp must be timezone-aware")
    payload: dict[str, Any] = {"confidence": event.confidence, "state": "started"}
    if event.posture and event.posture != "none":
        payload["posture"] = event.posture
    return {
        "source": "phone_cv",
        "type": "phone_usage_observed",
        "timestamp": event.started_at.isoformat(),
        "eventId": str(uuid4()),
        "payload": payload,
    }


class AscendObservationDispatcher:
    """A bounded, best-effort worker which keeps CV inference off the network path."""

    def __init__(self, client: AscendClient, character_id: str, *, max_queue_size: int = 32):
        if not isinstance(character_id, str) or not character_id.strip():
            raise ValueError("Ascend observation character ID must be nonempty")
        self._client = client
        self._character_id = character_id.strip()
        self._queue: Queue[dict[str, Any] | object] = Queue(maxsize=max_queue_size)
        self._thread = Thread(target=self._run, name="ascend-observations", daemon=True)
        self._thread.start()

    def submit_phone_usage(self, event: HoldEvent) -> bool:
        observation = build_phone_usage_observation(event)
        try:
            self._queue.put_nowait(observation)
            return True
        except Full:
            LOG.warning("Ascend observation queue full; dropping phone_usage_observed")
            return False

    def close(self) -> None:
        try:
            self._queue.put_nowait(_STOP)
        except Full:
            LOG.warning("Ascend observation queue full during shutdown")
            return
        self._thread.join(timeout=1.0)

    def flush_for_test(self) -> None:
        """Wait for queued work; intended for deterministic unit tests only."""
        self._queue.join()

    def _run(self) -> None:
        while True:
            observation = self._queue.get()
            try:
                if observation is _STOP:
                    return
                assert isinstance(observation, dict)
                result = self._client.send_event(
                    observation["type"], observation["payload"],
                    source=observation["source"],
                    timestamp=datetime.fromisoformat(observation["timestamp"]),
                    character_id=self._character_id,
                    event_id=observation["eventId"],
                )
                if result.state is not AscendConnectionState.CONNECTED:
                    LOG.debug("Ascend observation delivery not acknowledged: %s", result.state.value)
            except Exception as exc:
                LOG.warning("Ascend observation delivery failed: %s", exc)
            finally:
                self._queue.task_done()
