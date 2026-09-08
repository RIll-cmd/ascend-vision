"""OS-backed storage for the short-lived Core automation credential."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Protocol


class CredentialBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class VisionToken:
    access_token: str = field(repr=False)
    expires_at: datetime

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return self.expires_at <= current


class VisionTokenStore:
    """Stores only the 15-minute Vision token in the platform credential vault."""

    _USERNAME = "automation-user-token"

    def __init__(self, *, keyring_backend: CredentialBackend | None = None,
                 service_name: str = "ascend-vision.core-automation"):
        if keyring_backend is None:
            import keyring
            keyring_backend = keyring
        self._keyring = keyring_backend
        self._service_name = service_name

    def save(self, token: VisionToken) -> None:
        if not isinstance(token.access_token, str) or not token.access_token.strip():
            raise ValueError("Vision access token must be nonempty")
        if token.is_expired():
            self.clear()
            return
        payload = json.dumps({
            "accessToken": token.access_token,
            "expiresAt": token.expires_at.astimezone(timezone.utc).isoformat(),
        }, separators=(",", ":"))
        self._keyring.set_password(self._service_name, self._USERNAME, payload)

    def load(self) -> VisionToken | None:
        raw = self._keyring.get_password(self._service_name, self._USERNAME)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            access_token = payload["accessToken"]
            expires_at = datetime.fromisoformat(payload["expiresAt"])
            if expires_at.tzinfo is None:
                raise ValueError("expiresAt must include a timezone")
            token = VisionToken(access_token, expires_at.astimezone(timezone.utc))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.clear()
            return None
        if token.is_expired():
            self.clear()
            return None
        return token

    def clear(self) -> None:
        try:
            self._keyring.delete_password(self._service_name, self._USERNAME)
        except Exception:
            # Clearing an absent entry is not an application failure. Never log secrets.
            pass
