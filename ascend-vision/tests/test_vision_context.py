from datetime import datetime, timedelta, timezone
import json

import pytest

from integrations.vision_context import VisionAuthContext, VisionContextStore
from integrations.vision_token_store import VisionToken


class MemoryKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, value):
        self.values[(service, username)] = value

    def delete_password(self, service, username):
        self.values.pop((service, username), None)


def future_expiry():
    return datetime.now(timezone.utc) + timedelta(minutes=15)


def past_expiry():
    return datetime.now(timezone.utc) - timedelta(minutes=1)


def test_valid_saved_context_returns_authoritative_character():
    keyring = MemoryKeyring()
    store = VisionContextStore(keyring_backend=keyring, service_name="test-vision")
    saved_expiry = future_expiry()

    store.save(VisionAuthContext("guest-character", "Guest_d8d7", saved_expiry))

    loaded = store.load()
    assert loaded.character_id == "guest-character"
    assert loaded.character_name == "Guest_d8d7"
    assert loaded.expires_at == saved_expiry.astimezone(timezone.utc)
    serialized = json.loads(next(iter(keyring.values.values())))
    assert serialized.keys() == {"characterId", "characterName", "expiresAt"}
    assert all("token" not in key.lower() for key in serialized)


def test_expired_context_is_cleared_with_expired_token():
    keyring = MemoryKeyring()
    store = VisionContextStore(keyring_backend=keyring, service_name="test-vision")
    store.save(VisionAuthContext("guest-character", None, future_expiry()))
    service, username = next(iter(keyring.values))
    keyring.values[(service, username)] = json.dumps({
        "characterId": "guest-character",
        "characterName": None,
        "expiresAt": past_expiry().isoformat(),
    })

    assert store.load() is None
    assert keyring.values == {}


def test_malformed_persisted_context_is_cleared():
    keyring = MemoryKeyring()
    store = VisionContextStore(keyring_backend=keyring, service_name="test-vision")
    keyring.set_password("test-vision", "automation-auth-context", "not-json")

    assert store.load() is None
    assert keyring.values == {}


def test_naive_expiry_is_rejected_on_save():
    store = VisionContextStore(keyring_backend=MemoryKeyring(), service_name="test-vision")

    with pytest.raises(ValueError, match="timezone"):
        store.save(VisionAuthContext("guest-character", None, datetime.now()))


def test_naive_persisted_expiry_is_cleared_on_load():
    keyring = MemoryKeyring()
    store = VisionContextStore(keyring_backend=keyring, service_name="test-vision")
    keyring.set_password("test-vision", "automation-auth-context", json.dumps({
        "characterId": "guest-character",
        "characterName": None,
        "expiresAt": datetime.now().isoformat(),
    }))

    assert store.load() is None
    assert keyring.values == {}


def test_synced_character_overrides_stale_environment_character():
    from integrations.vision_context import resolve_character_id

    expiry = future_expiry()
    context = VisionAuthContext("guest-character", "Guest_d8d7", expiry)
    token = VisionToken("vision-token", expiry)

    assert resolve_character_id("stale-character", context, token) == "guest-character"


def test_stale_context_cannot_override_fallback_after_token_invalidation():
    from integrations.vision_context import resolve_character_id

    context = VisionAuthContext("stale-guest", "Guest_d8d7", future_expiry())

    assert resolve_character_id("configured-character", context, None) == "configured-character"


def test_missing_synced_context_uses_configured_character_as_fallback():
    from integrations.vision_context import resolve_character_id

    assert resolve_character_id("configured-character", None) == "configured-character"


def test_expired_synced_context_does_not_override_configured_character():
    from integrations.vision_context import resolve_character_id

    context = VisionAuthContext("expired-guest", None, past_expiry())

    assert resolve_character_id("configured-character", context) == "configured-character"
