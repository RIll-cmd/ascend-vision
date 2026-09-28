import os
from uuid import uuid4
from multiprocessing.connection import AuthenticationError

import pytest

from assistant.context_runtime import ContextRuntime
from integrations.context_ipc import ContextPipeClient, ContextPipeServer, PIPE_PREFIX


pytestmark = pytest.mark.skipif(os.name != "nt", reason="Phase 1 dashboard IPC uses a Windows named pipe")


def test_dashboard_reads_snapshot_and_sends_ephemeral_intent_over_owner_pipe():
    pipe_name = PIPE_PREFIX + "test-" + uuid4().hex
    runtime = ContextRuntime(device_id="test-laptop", boot_id="test-boot",
                             desk_calibration_available=True)
    authkey = uuid4().bytes + uuid4().bytes
    server = ContextPipeServer(runtime, pipe_name=pipe_name, authkey=authkey)
    server.start()
    client = ContextPipeClient(pipe_name=pipe_name, authkey=authkey)
    try:
        first = client.snapshot()
        assert first["fields"]["deskPresence"]["value"] == "unknown"
        assert first["fields"]["desktopActivity"]["value"] == "unavailable"
        assert client.desk_region_status() == {
            "available": True, "calibrated": False, "calibrating": False, "region": None,
        }
        runtime.record_companion_decision({
            "evaluated_at": "2026-09-28T10:00:00+00:00", "mode": "shadow",
            "rules": [{"rule_id": "desk_check_in", "trigger": "presence_not_fresh",
                       "source": None, "evidence_age_seconds": None, "channel": None,
                       "outcome": "not_eligible", "reason_code": "presence_not_fresh"}],
        })
        assert client.companion_decisions()[0]["mode"] == "shadow"
        assert client.begin_desk_calibration() is True
        assert client.desk_region_status()["calibrating"] is True
        client.cancel_desk_calibration()
        assert client.desk_region_status()["calibrating"] is False

        client.declare_intent("research", duration_seconds=120)
        updated = client.snapshot()
        assert updated["fields"]["declaredIntent"]["value"] == "research"
        assert updated["fields"]["declaredIntent"]["source"] == "user_declaration"
        assert updated["snapshot_id"] != first["snapshot_id"]

        client.set_snooze(duration_seconds=1800)
        assert client.snapshot()["snooze_until"] is not None
        client.clear_intent()
        assert client.snapshot()["fields"]["declaredIntent"]["value"] == "none"
        assert client.snapshot()["snooze_until"] is not None
        client.clear_snooze()
        assert client.snapshot()["snooze_until"] is None

        client.set_paused(True)
        paused = client.snapshot()
        assert paused["paused"] is True
        assert paused["fields"]["declaredIntent"]["value"] == "none"
        client.set_paused(False)
        assert client.snapshot()["paused"] is False

        client.clear()
        cleared = client.snapshot()
        assert cleared["fields"]["declaredIntent"]["value"] == "none"
    finally:
        server.close()


def test_pipe_rejects_another_owner_authentication_key():
    pipe_name = PIPE_PREFIX + "test-" + uuid4().hex
    server = ContextPipeServer(
        ContextRuntime(device_id="test-laptop"), pipe_name=pipe_name, authkey=b"owner-key" * 4,
    )
    server.start()
    client = ContextPipeClient(pipe_name=pipe_name, authkey=b"other-user" * 4)
    try:
        with pytest.raises((OSError, EOFError, AuthenticationError)):
            client.snapshot()
    finally:
        server.close()


def test_default_pipe_identity_connects_the_current_windows_owner():
    server = ContextPipeServer(ContextRuntime(device_id="test-laptop"))
    server.start()
    client = ContextPipeClient()
    try:
        assert client.snapshot()["device_id"] == "test-laptop"
    finally:
        server.close()


