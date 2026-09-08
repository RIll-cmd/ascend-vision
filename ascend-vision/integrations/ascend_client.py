"""HTTP-only client for the Real Ascend integration API."""
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import uuid
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from integrations.vision_token_store import VisionToken, VisionTokenStore
from integrations.vision_context import VisionContextStore, clear_vision_authorization


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
                 login_path: str = "/api/auth/login",
                 vision_token_path: str = "/api/auth/vision-token",
                 automation_capabilities_path: str = "/api/automations/capabilities",
                 eligible_habits_path: str = "/api/automations/eligible-habits",
                  automation_validation_path: str = "/api/automations/proposals/validate",
                  automations_path: str = "/api/automations",
                  token_store: VisionTokenStore | None = None,
                  context_store: VisionContextStore | None = None,
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
        self.login_path = self._path(login_path)
        self.vision_token_path = self._path(vision_token_path)
        self.automation_capabilities_path = self._path(automation_capabilities_path)
        self.eligible_habits_path = self._path(eligible_habits_path)
        self.automation_validation_path = self._path(automation_validation_path)
        self.automations_path = self._path(automations_path)
        self._token_store = token_store or VisionTokenStore()
        self._context_store = context_store
        self._opener = opener

    @staticmethod
    def _path(value: str) -> str:
        if not isinstance(value, str) or not value.startswith("/"):
            raise ValueError("Ascend API paths must start with '/'")
        return value

    def get_status(self) -> AscendResult:
        return self._request("GET", self.health_path)

    def get_vision_status(self, character_id: str | None) -> AscendResult:
        """Check Core reachability through the authenticated Vision status contract."""
        if not isinstance(character_id, str) or not character_id.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR,
                                error="ASCEND_CHARACTER_ID is not configured")
        path = "/api/integration/vision/status?" + urlencode({"characterId": character_id.strip()})
        return self._vision_request("GET", path)

    def send_command(self, text: str, *, source: str, character_id: str | None,
                     timestamp: datetime | None = None, request_id: str | None = None) -> AscendResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Ascend command text must be nonempty")
        if not isinstance(character_id, str) or not character_id.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR, error="ASCEND_CHARACTER_ID is not configured")
        if source not in ('phone', 'watch', 'ascend_vision'):
            raise ValueError("Ascend command source must be phone, watch or ascend_vision")
        sent_at = timestamp or datetime.now().astimezone()
        if sent_at.tzinfo is None:
            sent_at = sent_at.astimezone()
        payload = {"source": source, "characterId": character_id.strip(), "text": text.strip(),
                   "timestamp": sent_at.isoformat(), "requestId": request_id or f"{source}-{uuid.uuid4()}"}
        return self._request("POST", self.command_path, payload)

    def send_vision_heartbeat(self, *, character_id: str | None, device_id: str | None,
                              version: str, timestamp: datetime | None = None) -> AscendResult:
        """Report only safe Vision presence metadata using the Phase 6 Vision token."""
        if not isinstance(character_id, str) or not character_id.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR,
                                error="ASCEND_CHARACTER_ID is not configured")
        if not isinstance(device_id, str) or not device_id.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR,
                                error="ASCEND_DEVICE_ID is not configured")
        if not isinstance(version, str) or not version.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR,
                                error="Vision version is not configured")
        sent_at = timestamp or datetime.now(timezone.utc)
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=timezone.utc)
        payload = {
            "source": "ascend_vision",
            "characterId": character_id.strip(),
            "deviceId": device_id.strip(),
            "timestamp": sent_at.isoformat(),
            "version": version.strip(),
        }
        return self._vision_request("POST", "/api/integration/vision/heartbeat", payload)

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

    def obtain_vision_token(self, upstream_user_token: str) -> AscendResult:
        """Exchange an existing, in-memory Core user credential for the 15-minute Vision token.

        The upstream token is deliberately never persisted. The returned Vision token is
        stored by ``VisionTokenStore`` in the OS credential vault.
        """
        if not isinstance(upstream_user_token, str) or not upstream_user_token.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR,
                                error="A current Core user handoff is required.")
        result = self._user_request("POST", self.vision_token_path, bearer_token=upstream_user_token.strip())
        if result.state is not AscendConnectionState.CONNECTED:
            return result
        payload = result.payload or {}
        try:
            if payload.get("tokenType") != "Bearer":
                raise ValueError("unexpected token type")
            access_token = payload["accessToken"]
            expires_at = datetime.fromisoformat(payload["expiresAt"])
            if expires_at.tzinfo is None or not isinstance(access_token, str) or not access_token:
                raise ValueError("invalid token response")
            token = VisionToken(access_token, expires_at.astimezone(timezone.utc))
            if token.is_expired() or payload.get("expiresIn") != 900:
                raise ValueError("invalid token expiry")
            self._token_store.save(token)
        except (KeyError, TypeError, ValueError):
            return AscendResult(AscendConnectionState.SERVER_ERROR, error="Core returned an invalid Vision token handoff.")
        return result

    def login_and_obtain_vision_token(self, identifier: str, password: str) -> AscendResult:
        """Perform an intentional user login handoff without retaining the web token.

        Callers must collect credentials through a deliberate local UI, never voice or
        LLM text. The normal Core login token exists only for this method's exchange.
        """
        if not isinstance(identifier, str) or not identifier.strip() or not isinstance(password, str) or not password:
            return AscendResult(AscendConnectionState.CONFIG_ERROR, error="Core sign-in details are required.")
        login = self._http_request("POST", self.login_path,
                                   {"identifier": identifier.strip(), "password": password})
        if login.state is not AscendConnectionState.CONNECTED:
            return login
        payload = login.payload or {}
        upstream_token = payload.get("token")
        if not isinstance(upstream_token, str) or not upstream_token:
            return AscendResult(AscendConnectionState.SERVER_ERROR, error="Core returned an invalid sign-in handoff.")
        return self.obtain_vision_token(upstream_token)

    def get_automation_capabilities(self) -> AscendResult:
        return self._vision_request("GET", self.automation_capabilities_path)

    def get_eligible_habits(self, character_id: str) -> AscendResult:
        if not isinstance(character_id, str) or not character_id.strip():
            return AscendResult(AscendConnectionState.CONFIG_ERROR, error="ASCEND_CHARACTER_ID is not configured")
        path = f"{self.eligible_habits_path}?{urlencode({'characterId': character_id.strip()})}"
        return self._vision_request("GET", path)

    def validate_automation_proposal(self, proposal: dict[str, Any]) -> AscendResult:
        if not isinstance(proposal, dict):
            raise ValueError("Automation proposal must be a dictionary")
        return self._vision_request("POST", self.automation_validation_path, proposal)

    def create_automation(self, proposal: dict[str, Any]) -> AscendResult:
        if not isinstance(proposal, dict):
            raise ValueError("Automation proposal must be a dictionary")
        return self._vision_request("POST", self.automations_path, proposal)

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
        return self._http_request(method, path, payload, integration_key=self.api_token)

    def _vision_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> AscendResult:
        token = self._token_store.load()
        if token is None:
            return AscendResult(AscendConnectionState.AUTH_ERROR, error="Vision authentication is required.")
        result = self._user_request(method, path, payload, bearer_token=token.access_token)
        if result.state is AscendConnectionState.AUTH_ERROR and result.status_code == 401:
            clear_vision_authorization(self._token_store,
                                       self._context_store or VisionContextStore())
            return AscendResult(AscendConnectionState.AUTH_ERROR, status_code=401,
                                error="Vision authentication is required.")
        return result

    def _user_request(self, method: str, path: str, payload: dict[str, Any] | None = None,
                      *, bearer_token: str) -> AscendResult:
        return self._http_request(method, path, payload, bearer_token=bearer_token)

    def _http_request(self, method: str, path: str, payload: dict[str, Any] | None = None, *,
                      integration_key: str | None = None, bearer_token: str | None = None) -> AscendResult:
        body = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if integration_key:
            headers["X-Integration-Key"] = integration_key
        if bearer_token:
            headers["Authorization"] = f"Bearer {bearer_token}"
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
