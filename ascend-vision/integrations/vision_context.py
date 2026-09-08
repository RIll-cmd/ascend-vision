"""OS-backed storage for non-secret authenticated Vision character context."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json

from integrations.vision_token_store import CredentialBackend, VisionToken, VisionTokenStore


@dataclass(frozen=True)
class VisionAuthContext:
    character_id: str
    character_name: str | None
    expires_at: datetime

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return self.expires_at <= current


def resolve_character_id(configured_character_id: str | None,
                         synced_context: VisionAuthContext | None,
                         synced_token: VisionToken | None = None) -> str | None:
    """Prefer Core context only when paired with the same valid Vision token."""
    if (synced_context is not None
            and not synced_context.is_expired()
            and synced_token is not None
            and synced_token.expires_at.tzinfo is not None
            and not synced_token.is_expired()
            and synced_context.expires_at == synced_token.expires_at
            and isinstance(synced_context.character_id, str)
            and synced_context.character_id.strip()):
        return synced_context.character_id.strip()
    if isinstance(configured_character_id, str) and configured_character_id.strip():
        return configured_character_id.strip()
    return None


class VisionContextStore:
    """Stores only safe character metadata aligned with the Vision token expiry."""

    _USERNAME = "automation-auth-context"

    def __init__(self, *, keyring_backend: CredentialBackend | None = None,
                 service_name: str = "ascend-vision.core-automation"):
        if keyring_backend is None:
            import keyring
            keyring_backend = keyring
        self._keyring = keyring_backend
        self._service_name = service_name

    def save(self, context: VisionAuthContext) -> None:
        if not isinstance(context.character_id, str) or not context.character_id.strip():
            raise ValueError("Vision character ID must be nonempty")
        if context.expires_at.tzinfo is None:
            raise ValueError("Vision context expiry must include a timezone")
        if context.is_expired():
            self.clear()
            return
        payload = json.dumps({
            "characterId": context.character_id.strip(),
            "characterName": context.character_name,
            "expiresAt": context.expires_at.astimezone(timezone.utc).isoformat(),
        }, separators=(",", ":"))
        self._keyring.set_password(self._service_name, self._USERNAME, payload)

    def load(self) -> VisionAuthContext | None:
        raw = self._keyring.get_password(self._service_name, self._USERNAME)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
            character_id = payload["characterId"]
            character_name = payload.get("characterName")
            expires_at = datetime.fromisoformat(payload["expiresAt"])
            if (not isinstance(character_id, str) or not character_id.strip()
                    or character_name is not None and not isinstance(character_name, str)
                    or expires_at.tzinfo is None):
                raise ValueError("invalid Vision context")
            context = VisionAuthContext(
                character_id.strip(), character_name, expires_at.astimezone(timezone.utc),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self.clear()
            return None
        if context.is_expired():
            self.clear()
            return None
        return context

    def clear(self) -> None:
        try:
            self._keyring.delete_password(self._service_name, self._USERNAME)
        except Exception:
            # Clearing an absent entry is not an application failure. Never log credentials.
            pass


def clear_vision_authorization(token_store: VisionTokenStore,
                               context_store: VisionContextStore) -> None:
    """Best-effort invalidation of the paired Vision token and character context."""
    for store in (token_store, context_store):
        try:
            store.clear()
        except Exception:
            # Cleanup must not turn a rejected-token response into an exception.
            pass
