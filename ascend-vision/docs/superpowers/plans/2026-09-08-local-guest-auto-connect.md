# Local Guest Session Auto-Connect Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let local Ascend Vision reuse an authenticated Core Guest browser session to obtain the existing short-lived Vision token and authoritative character context.

**Architecture:** Browser code at `http://localhost:8765` uses browser-managed Core cookies with `credentials: "include"` to obtain `/api/auth/me` and `/api/auth/vision-token`. It sends the short-lived Vision token only to Vision's loopback backend, which stores it and safe character metadata through a local credential/context boundary. `main.py` resolves that context before starting user-scoped Core services and the heartbeat.

**Tech Stack:** Python, Flask, vanilla browser JavaScript, Windows Credential Manager/keyring, pytest.

**Spec:** `docs/superpowers/specs/2026-09-08-local-guest-auto-connect-design.md`

## Global Constraints

- Modify only `D:\ascend-vision`; never modify Ascend Core.
- Local auto-connect is enabled only for localhost Core URLs and must use `http://localhost:8765`.
- Never read, log, render, URL-encode, locally store, or send Core cookies, normal Core JWTs, integration keys, or passwords to an LLM.
- Persist only the Phase 6 Vision JWT through `VisionTokenStore` and safe character metadata through the local context store.
- Preserve the production password handoff, Gesture Controls v1, CV dispatch, Phase 6 validation, and confirmation behavior.

---

### Task 1: Create an authenticated local Vision context boundary

**Files:**
- Modify: `integrations/vision_token_store.py`
- Create: `integrations/vision_context.py`
- Test: `tests/test_vision_context.py`

**Interfaces:**
- Consumes: `VisionToken(access_token: str, expires_at: datetime)` from `VisionTokenStore`.
- Produces: `VisionAuthContext(character_id: str, character_name: str | None, expires_at: datetime)` and `VisionContextStore.load()`, `save()`, `clear()`.

- [ ] **Step 1: Write failing tests**

```python
def test_valid_saved_context_returns_authoritative_character():
    store = VisionContextStore(keyring_backend=MemoryKeyring(), service_name="test-vision")
    store.save(VisionAuthContext("guest-character", "Guest_d8d7", future_expiry()))
    assert store.load().character_id == "guest-character"

def test_expired_context_is_cleared_with_expired_token():
    store = VisionContextStore(keyring_backend=MemoryKeyring(), service_name="test-vision")
    store.save(VisionAuthContext("guest-character", None, past_expiry()))
    assert store.load() is None
```

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_vision_context.py -q`

Expected: import failure because `VisionAuthContext` and `VisionContextStore` do not exist.

- [ ] **Step 3: Implement minimal credential-backed context storage**

```python
@dataclass(frozen=True)
class VisionAuthContext:
    character_id: str
    character_name: str | None
    expires_at: datetime

class VisionContextStore:
    def load(self) -> VisionAuthContext | None: ...
    def save(self, context: VisionAuthContext) -> None: ...
    def clear(self) -> None: ...
```

Store only JSON character metadata and expiry in the credential backend. Never serialize the access token in this class.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_vision_context.py -q`

Expected: PASS.

### Task 2: Add loopback-only token/context persistence endpoints

**Files:**
- Modify: `dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: browser `POST /api/auth/local-vision-handoff` JSON `{accessToken, expiresAt, character}`.
- Produces: a safe status-only response `{status, character, expiresAt}`; does not return a token.

- [ ] **Step 1: Write failing tests**

```python
def test_local_handoff_stores_only_vision_token_and_core_character(client, stores):
    response = client.post('/api/auth/local-vision-handoff', json={
        'accessToken': 'short-lived-vision-token',
        'expiresAt': future_iso(),
        'character': {'id': 'guest-character', 'name': 'Guest_d8d7'},
    })
    assert response.status_code == 200
    assert response.json['character']['id'] == 'guest-character'
    assert 'accessToken' not in response.json

def test_local_handoff_rejects_missing_or_non_loopback_context(client):
    assert client.post('/api/auth/local-vision-handoff', json={}).status_code == 400
```

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_dashboard.py -k local_handoff -q`

Expected: endpoint missing.

- [ ] **Step 3: Implement the local persistence endpoint**

Validate the exact token, expiry, and character shape; instantiate existing `VisionTokenStore` and `VisionContextStore`; store both only after validation; return only safe status metadata. Reject non-loopback host headers and non-local Core configuration. Do not log request bodies.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_dashboard.py -k local_handoff -q`

Expected: PASS.

### Task 3: Implement local browser auto-connect UI while preserving production login

**Files:**
- Modify: `dashboard.py`
- Modify: `templates/dashboard.html`
- Modify: `static/dashboard.js`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: template boolean `local_auto_connect`, Core URL, and status endpoint response.
- Produces: local status panel and `POST /api/auth/local-vision-handoff` only after successful Core browser requests.

- [ ] **Step 1: Write failing tests**

```python
def test_local_dashboard_hides_password_form_and_includes_auto_connect_controls(client):
    page = client.get('/')
    assert b'Retry Connection' in page.data
    assert b'core-password' not in page.data

def test_nonlocal_dashboard_keeps_existing_password_handoff_form(client, nonlocal_config):
    page = create_app(nonlocal_config).test_client().get('/')
    assert b'core-password' in page.data
```

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_dashboard.py -k "local_dashboard or nonlocal_dashboard" -q`

Expected: local panel assertion fails.

- [ ] **Step 3: Implement local UI and browser handoff**

For local Core URLs, render status panel rather than password fields. Browser JavaScript must call `${coreUrl}/api/auth/me` and `${coreUrl}/api/auth/vision-token` with `credentials: 'include'`; it must discard the token after posting to Vision's local endpoint. Always open the dashboard browser URL as `http://localhost:<port>`. For non-local URLs, retain the existing username/password handoff form and code path.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_dashboard.py -k "local_dashboard or nonlocal_dashboard" -q`

Expected: PASS.

### Task 4: Resolve authoritative runtime character context in Vision services

**Files:**
- Modify: `main.py`
- Modify: `integrations/vision_heartbeat.py` only if required to accept refreshed context safely
- Test: `tests/test_vision_context.py`, `tests/test_vision_heartbeat.py`, `tests/test_automation_proposals.py`

**Interfaces:**
- Consumes: valid `VisionAuthContext`.
- Produces: resolved `ascend_character_id` used by observations, automation, commands, and the single heartbeat worker.

- [ ] **Step 1: Write failing tests**

```python
def test_synced_character_overrides_stale_environment_character():
    assert resolve_character_id('stale-character', VisionAuthContext('guest-character', None, future_expiry())) == 'guest-character'

def test_heartbeat_uses_resolved_authenticated_character():
    assert heartbeat_payload_for('stale-character', synced_guest_context())['character_id'] == 'guest-character'
```

- [ ] **Step 2: Run tests to verify RED**

Run: `pytest tests/test_vision_context.py tests/test_vision_heartbeat.py -q`

Expected: resolver missing.

- [ ] **Step 3: Implement character resolution**

Create a pure resolver that prefers a valid synchronized context over `ASCEND_CHARACTER_ID`. Use it before constructing observations, automation proposals, and heartbeat. Leave the configured environment value as fallback only. Do not start a second worker or mutate Core.

- [ ] **Step 4: Run tests to verify GREEN**

Run: `pytest tests/test_vision_context.py tests/test_vision_heartbeat.py tests/test_automation_proposals.py -q`

Expected: PASS.

### Task 5: Regression and safety verification

**Files:**
- Test: `tests/test_dashboard.py`
- Test: `tests/test_ascend_client.py`
- Test: `tests/test_gesture_controls.py`
- Test: `tests/test_vision_heartbeat.py`

- [ ] **Step 1: Add safety assertions**

```python
def test_local_status_response_never_contains_token(client):
    assert b'accessToken' not in client.get('/api/auth/local-vision-status').data

def test_existing_automation_client_still_uses_stored_vision_token():
    # Existing Phase 6 Bearer-token assertion remains unchanged.
    ...
```

- [ ] **Step 2: Run focused suite**

Run: `pytest tests/test_vision_context.py tests/test_dashboard.py tests/test_ascend_client.py tests/test_vision_heartbeat.py tests/test_automation_proposals.py tests/test_gesture_controls.py -q`

Expected: PASS, subject only to documented missing optional CV/dashboard dependencies.

- [ ] **Step 3: Run source checks**

Run: `python -m py_compile main.py dashboard.py integrations/vision_context.py integrations/vision_heartbeat.py && git diff --check`

Expected: exit code 0.

