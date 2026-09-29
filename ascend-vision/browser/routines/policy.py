"""A routine restriction layer that can only narrow an existing browser policy."""

from __future__ import annotations

from urllib.parse import urlsplit

from browser.policy import PolicyDenied
from browser.routines.contracts import RoutineContractError, safe_decoded_path


class RoutinePolicy:
    def __init__(self, base_policy, *, allowed_origins, path_prefixes, allowed_actions):
        self._base = base_policy
        self._origins = frozenset(allowed_origins)
        self._path_prefixes = tuple(path_prefixes)
        self._actions = frozenset(allowed_actions)

    def validate_url(self, url: str) -> str:
        normalized = self._base.validate_url(url)
        parsed = urlsplit(normalized)
        origin = f'{parsed.scheme.lower()}://{parsed.hostname.rstrip(".").encode("idna").decode("ascii").lower()}'
        if origin not in self._origins:
            raise PolicyDenied('This destination is outside the routine’s reviewed origins.')
        try:
            decoded = safe_decoded_path(parsed.path)
        except RoutineContractError as exc:
            raise PolicyDenied('This destination has an ambiguous path.')
        if not any(decoded.startswith(prefix) for prefix in self._path_prefixes):
            raise PolicyDenied('This destination is outside the routine’s supported paths.')
        return normalized

    def allows_action(self, action: str, *, target_kind: str | None) -> bool:
        return action in self._actions and self._base.allows_action(action, target_kind=target_kind)
