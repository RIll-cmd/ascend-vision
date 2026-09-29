"""Owner-scoped storage for explicit Playwright login-state saves."""

import hashlib
import json
import os
from pathlib import Path
import re
import secrets


MAX_PROFILE_STATE_BYTES = 1_000_000
_PROFILE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')


def _default_root() -> Path:
    local_app_data = os.environ.get('LOCALAPPDATA')
    if os.name != 'nt' or not local_app_data:
        raise RuntimeError('Saved browser profiles require Windows owner storage.')
    return Path(local_app_data) / 'AscendVision' / 'browser' / 'profiles'


def _dpapi_protect(data: bytes) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Saved browser profiles require Windows DPAPI.')
    try:
        import win32crypt
        _description, protected = win32crypt.CryptProtectData(
            data, 'Ascend Vision browser profile', None, None, None, 0,
        )
        return bytes(protected)
    except Exception as exc:
        raise RuntimeError('Windows DPAPI could not protect the browser profile.') from exc


def _dpapi_unprotect(data: bytes) -> bytes:
    if os.name != 'nt':
        raise RuntimeError('Saved browser profiles require Windows DPAPI.')
    try:
        import win32crypt
        _description, clear = win32crypt.CryptUnprotectData(data, None, None, None, 0)
        return bytes(clear)
    except Exception as exc:
        raise RuntimeError('Windows DPAPI could not unlock the browser profile for this owner.') from exc


class BrowserProfileStore:
    """Stores only explicitly approved storage-state snapshots, encrypted per Windows user."""

    def __init__(self, root: str | Path | None = None, *, protect=None, unprotect=None,
                 max_state_bytes: int = MAX_PROFILE_STATE_BYTES):
        self._root = Path(root) if root is not None else _default_root()
        self._protect = protect or _dpapi_protect
        self._unprotect = unprotect or _dpapi_unprotect
        if type(max_state_bytes) is not int or not 1 <= max_state_bytes <= MAX_PROFILE_STATE_BYTES:
            raise ValueError('max_state_bytes is outside the safe profile limit')
        self._max_state_bytes = max_state_bytes

    def _path(self, owner: str, profile_id: str) -> Path:
        if not isinstance(owner, str) or not owner.strip() or len(owner) > 128:
            raise ValueError('profile owner is invalid')
        if not isinstance(profile_id, str) or not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError('profile_id is invalid')
        owner_key = hashlib.sha256(owner.casefold().encode('utf-8')).hexdigest()
        return self._root / owner_key / f'{profile_id}.dpapi'

    def save(self, owner: str, profile_id: str, state: dict) -> None:
        if not isinstance(state, dict):
            raise ValueError('profile state must be an object')
        raw = json.dumps(state, ensure_ascii=False, allow_nan=False,
                         separators=(',', ':')).encode('utf-8')
        if len(raw) > self._max_state_bytes:
            raise ValueError('profile state exceeds the configured size limit')
        protected = self._protect(raw)
        if not isinstance(protected, bytes) or len(protected) > self._max_state_bytes * 2:
            raise ValueError('protected profile state is invalid or too large')
        path = self._path(owner, profile_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f'.{profile_id}-{secrets.token_hex(8)}.tmp')
        try:
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(protected)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def load(self, owner: str, profile_id: str) -> dict:
        path = self._path(owner, profile_id)
        try:
            protected = path.read_bytes()
        except FileNotFoundError as exc:
            raise FileNotFoundError('Saved browser profile does not exist.') from exc
        if len(protected) > self._max_state_bytes * 2:
            raise ValueError('protected profile state exceeds the configured size limit')
        raw = self._unprotect(protected)
        if not isinstance(raw, bytes) or len(raw) > self._max_state_bytes:
            raise ValueError('decrypted profile state exceeds the configured size limit')
        try:
            state = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError('saved browser profile is invalid') from exc
        if not isinstance(state, dict):
            raise ValueError('saved browser profile must be an object')
        return state

    def list_profiles(self, owner: str) -> list[str]:
        owner_key = self._path(owner, 'validation').parent
        if not owner_key.exists():
            return []
        return sorted(path.stem for path in owner_key.glob('*.dpapi') if _PROFILE_ID.fullmatch(path.stem))

    def clear(self, owner: str, profile_id: str) -> bool:
        path = self._path(owner, profile_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True
