"""Owner-scoped, in-memory dashboard IPC over a local Windows named pipe."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from multiprocessing.connection import Client, Listener

from assistant.context_runtime import ContextRuntime, MAX_EVENT_BYTES


MAX_MESSAGE_BYTES = MAX_EVENT_BYTES
# Construct the Windows pipe prefix without fragile backslash escaping.
PIPE_PREFIX = chr(92) * 2 + "." + chr(92) + "pipe" + chr(92) + "AscendVisionContext-"


def _current_user_sid() -> str:
    if os.name != "nt":
        raise OSError("Vision context named pipes are available only on Windows")
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        token_user = win32security.GetTokenInformation(token, win32security.TokenUser)
        return win32security.ConvertSidToStringSid(token_user[0])
    finally:
        token.Close()


def _default_pipe_identity() -> tuple[str, bytes]:
    sid = _current_user_sid()
    digest = hashlib.sha256(sid.encode("ascii")).hexdigest()[:24]
    authkey = hashlib.sha256(b"Ascend Vision context IPC v1\0" + sid.encode("ascii")).digest()
    return PIPE_PREFIX + digest, authkey


def _snapshot_payload(snapshot) -> dict:
    return {
        "schema_version": snapshot.schema_version,
        "snapshot_id": snapshot.snapshot_id,
        "device_id": snapshot.device_id,
        "boot_id": snapshot.boot_id,
        "generated_at": snapshot.generated_at.isoformat(),
        "paused": snapshot.paused,
        "snooze_until": snapshot.snooze_until.isoformat() if snapshot.snooze_until else None,
        "fields": {
            name: {
                "value": field.value,
                "source": field.source,
                "observed_at": field.observed_at.isoformat() if field.observed_at else None,
                "expires_at": field.expires_at.isoformat() if field.expires_at else None,
                "evidence_kind": field.evidence_kind,
                "confidence": field.confidence,
                "source_available": field.source_available,
                "freshness": field.freshness,
            }
            for name, field in snapshot.fields.items()
        },
    }


class ContextPipeServer:
    def __init__(self, runtime: ContextRuntime, *, pipe_name: str | None = None,
                 authkey: bytes | None = None):
        if pipe_name is None or authkey is None:
            default_name, default_key = _default_pipe_identity()
            pipe_name = pipe_name or default_name
            authkey = authkey or default_key
        if not isinstance(pipe_name, str) or not pipe_name.casefold().startswith(PIPE_PREFIX.casefold()):
            raise ValueError("context pipe name must use the Ascend Vision local namespace")
        if not isinstance(authkey, bytes) or len(authkey) < 32:
            raise ValueError("context pipe authentication key must contain at least 32 bytes")
        self.runtime = runtime
        self.pipe_name = pipe_name
        self.authkey = authkey
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener = None
        self._startup_error: Exception | None = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("context pipe server is already started")
        self._thread = threading.Thread(target=self._serve, name="vision-context-pipe", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            self.close()
            raise TimeoutError("context pipe server did not become ready")
        if self._startup_error is not None:
            raise RuntimeError("context pipe server could not start") from self._startup_error

    def _serve(self):
        try:
            self._listener = Listener(self.pipe_name, family="AF_PIPE", authkey=self.authkey)
            self._ready.set()
            while not self._stop.is_set():
                try:
                    connection = self._listener.accept()
                except (OSError, EOFError):
                    if self._stop.is_set():
                        break
                    continue
                try:
                    self._handle(connection)
                finally:
                    connection.close()
        except Exception as exc:
            self._startup_error = exc
            self._ready.set()
        finally:
            if self._listener is not None:
                self._listener.close()
                self._listener = None

    def _handle(self, connection):
        try:
            raw = connection.recv_bytes(MAX_MESSAGE_BYTES)
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict) or not isinstance(request.get("op"), str):
                raise ValueError("invalid request")
            if request == {"op": "snapshot"}:
                response = {"ok": True, "snapshot": _snapshot_payload(self.runtime.read_snapshot())}
            elif request == {"op": "desk_region_status"}:
                response = {"ok": True, "desk_region": self.runtime.desk_region_status()}
            elif request == {"op": "companion_decisions"}:
                response = {"ok": True, "decisions": self.runtime.read_companion_decisions()}
            elif request == {"op": "begin_desk_calibration"}:
                started = self.runtime.begin_desk_calibration()
                response = {"ok": True, "started": started}
            elif request == {"op": "cancel_desk_calibration"}:
                self.runtime.cancel_desk_calibration()
                response = {"ok": True}
            elif set(request) == {"op", "intent", "duration_seconds"} and request["op"] == "declare_intent":
                if isinstance(request["duration_seconds"], bool) or not isinstance(request["duration_seconds"], (int, float)):
                    raise ValueError("invalid declaration duration")
                declaration = self.runtime.declare_intent(
                    request["intent"], duration_seconds=request["duration_seconds"],
                )
                response = {"ok": True, "declaration_id": declaration.declaration_id}
            elif request == {"op": "clear"}:
                self.runtime.clear()
                response = {"ok": True}
            elif request == {"op": "clear_intent"}:
                self.runtime.clear_intent()
                response = {"ok": True}
            elif request == {"op": "clear_snooze"}:
                self.runtime.clear_snooze()
                response = {"ok": True}
            elif set(request) == {"op", "duration_seconds"} and request["op"] == "snooze":
                if isinstance(request["duration_seconds"], bool) or not isinstance(request["duration_seconds"], (int, float)):
                    raise ValueError("invalid snooze duration")
                self.runtime.set_snooze(duration_seconds=request["duration_seconds"])
                response = {"ok": True}
            elif set(request) == {"op", "paused"} and request["op"] == "pause":
                self.runtime.set_paused(request["paused"])
                response = {"ok": True}
            else:
                raise ValueError("unsupported context request")
        except Exception:
            response = {"ok": False, "error": "invalid context request"}
        payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
        if len(payload) > MAX_MESSAGE_BYTES:
            payload = b'{"ok":false,"error":"context response too large"}'
        connection.send_bytes(payload)

    def close(self):
        self._stop.set()
        listener = self._listener
        if listener is not None:
            listener.close()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._thread = None


class ContextPipeClient:
    def __init__(self, *, pipe_name: str | None = None, authkey: bytes | None = None):
        if pipe_name is None or authkey is None:
            default_name, default_key = _default_pipe_identity()
            pipe_name = pipe_name or default_name
            authkey = authkey or default_key
        if not isinstance(pipe_name, str) or not pipe_name.casefold().startswith(PIPE_PREFIX.casefold()):
            raise ValueError("context pipe name must use the Ascend Vision local namespace")
        if not isinstance(authkey, bytes) or len(authkey) < 32:
            raise ValueError("context pipe authentication key must contain at least 32 bytes")
        self.pipe_name = pipe_name
        self.authkey = authkey

    def _request(self, payload: dict) -> dict:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_MESSAGE_BYTES:
            raise ValueError("context request exceeds the 4 KiB limit")
        with Client(self.pipe_name, family="AF_PIPE", authkey=self.authkey) as connection:
            connection.send_bytes(raw)
            response = json.loads(connection.recv_bytes(MAX_MESSAGE_BYTES).decode("utf-8"))
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise ValueError("Vision context request was rejected")
        return response

    def snapshot(self) -> dict:
        return self._request({"op": "snapshot"})["snapshot"]

    def desk_region_status(self) -> dict:
        return self._request({"op": "desk_region_status"})["desk_region"]

    def companion_decisions(self) -> list[dict]:
        return self._request({"op": "companion_decisions"})["decisions"]

    def begin_desk_calibration(self) -> bool:
        return self._request({"op": "begin_desk_calibration"})["started"]

    def cancel_desk_calibration(self) -> None:
        self._request({"op": "cancel_desk_calibration"})

    def declare_intent(self, intent: str, *, duration_seconds: float = 900.0) -> str:
        response = self._request({
            "op": "declare_intent",
            "intent": intent,
            "duration_seconds": duration_seconds,
        })
        return response["declaration_id"]

    def clear(self) -> None:
        self._request({"op": "clear"})

    def clear_intent(self) -> None:
        self._request({"op": "clear_intent"})

    def set_snooze(self, *, duration_seconds: float = 1800.0) -> None:
        self._request({"op": "snooze", "duration_seconds": duration_seconds})

    def clear_snooze(self) -> None:
        self._request({"op": "clear_snooze"})

    def set_paused(self, paused: bool) -> None:
        if type(paused) is not bool:
            raise ValueError("paused must be a boolean")
        self._request({"op": "pause", "paused": paused})
