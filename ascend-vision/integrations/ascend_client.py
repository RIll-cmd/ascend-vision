"""HTTP-only client for the Real Ascend integration API."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import uuid
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOG = logging.getLogger(__name__)


class AscendConnectionState(str, Enum):
    CONNECTED = "ASCEND_CONNECTED"
    OFFLINE = "ASCEND_OFFLINE"
    AUTH_ERROR = "ASCEND_AUTH_ERROR"
    SERVER_ERROR = "ASCEND_SERVER_ERROR"
    CONFIG_ERROR = "ASCEND_CONFIG_ERROR"


@dataclass(frozen=True)
class AscendResult:
    state: AscendConnectionState
    payload: dict[str, Any] | None = None
    status_code: int | None = None
    error: str | None = None

    @property
    def message(self) -> str | None:
        if self.payload is None:
            return None
        message = self.payload.get("message")
        return message if isinstance(message, str) else None


class AscendClient:
    """A small, failure-contained boundary to Real Ascend's API."""

    def __init__(self, base_url: str, api_token: str | None = None, *,
                 timeout_seconds: float = 5.0,
                 health_path: str = "/api/integration/health",
                 command_path: str = "/api/integration/command",
                 event_path: str = "/api/integration/event",
                 opener: Callable = urlopen):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("Ascend base URL must be nonempty")
        if timeout_seconds <= 0:
            raise ValueError("Ascend timeout must be positive")
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token.strip() if api_token else None
        self.timeout_seconds = timeout_seconds
        self.health_path = self._path(health_path)
        self.command_path = self._path(command_path)
        self.event_path = self._path(event_path)
        self._opener = opener

    @staticmethod
    def _path(value: str) -> str:
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError("Ascend API paths must start with '/'")
        return value

    def get_status(self) -> AscendResult:
        return self._request("GET", self.health_path)

    def send_command(self, text: str, *, source: str, character_id: str | None,
                     timestamp: datetime | None = None, request_id: str | None = None) -> AscendResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Ascend command text must be nonempty")
        if not isinstance(character_id, str) or not character_id.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR, error="ASCEND_CHARACTER_ID is not configured")
        if source not in ('phone', 'watch'):
            raise ValueError("Ascend command source must be phone or watch")
        sent_at = timestamp or datetime.now().astimezone()
        if sent_at.tzinfo is None:
            sent_at = sent_at.astimezone()
        payload = {"source": source, "characterId": character_id.strip(), "text": text.strip(),
                   "timestamp": sent_at.isoformat(), "requestId": request_id or f"{source}-{uuid.uuid4()}"}
        return self._request("POST", self.command_path, payload)

    def send_event(self, event_type: str, payload: dict[str, Any], *, source: str,
                   timestamp: datetime | None = None, device_id: str | None = None,
                   character_id: str | None = None, event_id: str | None = None) -> AscendResult:
        if not isinstance(event_type, str) or not event_type.strip():
            raise ValueError("Ascend event type must be nonempty")
        if not isinstance(payload, dict):
            raise ValueError("Ascend event payload must be a dictionary")
        envelope = self._envelope(event_type, source, timestamp, device_id)
        envelope["payload"] = payload
        if character_id is not None:
            if not isinstance(character_id, str) or not character_id.strip():
                return AscendResult(AscendConnectionState.CONFIG_ERROR, error="ASCEND_CHARACTER_ID is not configured")
            envelope["characterId"] = character_id.strip()
        if event_id is not None:
            if not isinstance(event_id, str) or not event_id.strip():
                raise ValueError("Ascend event ID must be nonempty")
            envelope["eventId"] = event_id.strip()
        return self._request("POST", self.event_path, envelope)

    @staticmethod
    def _envelope(event_type: str, source: str, timestamp: datetime | None,
                  device_id: str | None) -> dict[str, Any]:
        if not isinstance(source, str) or not source.strip():
            raise ValueError("Ascend source must be nonempty")
        sent_at = timestamp or datetime.now(timezone.utc)
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        data: dict[str, Any] = {
            "source": source.strip(),
            "type": event_type.strip(),
            "timestamp": sent_at.isoformat(),
        }
        if device_id:
            data["device_id"] = device_id
        return data

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> AscendResult:
        body = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.api_token:
            headers["X-Integration-Key"] = self.api_token
        request = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        try:
            with self._opener(request, timeout=self.timeout_seconds) as response:
                return AscendResult(AscendConnectionState.CONNECTED, self._json(response.read()), response.status)
        except HTTPError as exc:
            if exc.code in (401, 403):
                return AscendResult(AscendConnectionState.AUTH_ERROR, self._error_payload(exc), exc.code, str(exc))
            return AscendResult(AscendConnectionState.SERVER_ERROR, self._error_payload(exc), exc.code, str(exc))
        except (URLError, TimeoutError, OSError) as exc:
            LOG.warning("Ascend API unavailable: %s", exc)
            return AscendResult(AscendConnectionState.OFFLINE, error=str(exc))

    @staticmethod
    def _json(body: bytes) -> dict[str, Any]:
        try:
            data = json.loads(body.decode("utf-8"))
            return data if isinstance(data, dict) else {"data": data}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def _error_payload(self, error: HTTPError) -> dict[str, Any]:
        try:
            return self._json(error.read())
        except OSError:
            return {}
