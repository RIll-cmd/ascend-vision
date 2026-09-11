"""Unit tests for CoreHeartbeatWorker."""
import time
from unittest.mock import AsyncMock, MagicMock

from integrations.core_heartbeat import CoreHeartbeatWorker


def test_heartbeat_worker_periodic_ping():
    mock_client = MagicMock()
    mock_client.send_heartbeat = AsyncMock(return_value={"status": "ok", "state": "CONNECTED"})

    callback = MagicMock()
    worker = CoreHeartbeatWorker(
        core_client=mock_client,
        interval_seconds=0.1,  # Fast interval for test
        callback=callback,
    )

    started = worker.start()
    assert started is True

    # Allow worker to run for 2-3 intervals
    time.sleep(0.35)
    worker.close()

    assert mock_client.send_heartbeat.call_count >= 2
    assert callback.call_count >= 2
    callback.assert_called_with({"status": "ok", "state": "CONNECTED"})


def test_heartbeat_worker_handles_failure_gracefully():
    mock_client = MagicMock()
    mock_client.send_heartbeat = AsyncMock(side_effect=Exception("Connection refused"))

    callback = MagicMock()
    worker = CoreHeartbeatWorker(
        core_client=mock_client,
        interval_seconds=0.1,
        callback=callback,
    )

    started = worker.start()
    assert started is True

    time.sleep(0.25)
    worker.close()

    assert callback.call_count >= 1
    call_arg = callback.call_args[0][0]
    assert call_arg.get("status") == "OFFLINE"
