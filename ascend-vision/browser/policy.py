"""Deterministic capability and destination checks for public browser research."""

import ipaddress
import socket
from urllib.parse import urlsplit


class PolicyDenied(ValueError):
    """Raised before an action violates the task's fixed research scope."""


class BrowserPolicy:
    _READ_ACTIONS = {'navigate', 'observe', 'scroll', 'back', 'wait_for', 'ask_user', 'finish'}

    def __init__(self, *, allow_test_origins: set[str] | frozenset[str] = frozenset(),
                 scope_mode: str = 'public_research', allowed_origins: set[str] | frozenset[str] = frozenset(),
                 capabilities: set[str] | frozenset[str] = frozenset()):
        self._allow_test_origins = frozenset(allow_test_origins)
        if scope_mode not in {'public_research', 'selected_origins'}:
            raise ValueError('unsupported browser scope')
        self._scope_mode = scope_mode
        self._allowed_origins = frozenset(self._origin(item) for item in allowed_origins)
        self._capabilities = frozenset(capabilities)
        supported = {'fill', 'select', 'click', 'download', 'upload'}
        if self._capabilities - supported:
            raise ValueError('unsupported browser capability')
        if scope_mode == 'public_research' and (self._allowed_origins or self._capabilities):
            raise ValueError('public research cannot add origins or mutation capabilities')
        if scope_mode == 'selected_origins' and not self._allowed_origins:
            raise ValueError('selected-origin scope requires at least one allowed origin')

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError('malformed browser origin') from exc
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username
                or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment
                or (port is not None and port != (80 if parsed.scheme == 'http' else 443))):
            raise ValueError('browser origin must be a standard-port HTTP(S) origin')
        origin = f'{parsed.scheme.lower()}://{parsed.hostname.rstrip(".").lower()}'
        if port is not None and port != (80 if parsed.scheme == 'http' else 443):
            origin += f':{port}'
        return origin

    def validate_url(self, url: str) -> str:
        if not isinstance(url, str) or not url or len(url) > 2_048:
            raise PolicyDenied('Destination URL is invalid or too long.')
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise PolicyDenied('Destination URL is malformed.') from exc
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None):
            raise PolicyDenied('Only public HTTP(S) pages on standard ports are allowed.')
        host = parsed.hostname.rstrip('.').lower()
        origin = f'{parsed.scheme}://{host}'
        if port is not None and port != (80 if parsed.scheme == 'http' else 443):
            origin += f':{port}'
        if origin in self._allow_test_origins:
            return url
        if port is not None and port != (80 if parsed.scheme == 'http' else 443):
            raise PolicyDenied('Only public HTTP(S) pages on standard ports are allowed.')
        if host == 'localhost' or host.endswith('.localhost') or host.endswith('.local'):
            raise PolicyDenied('Local network destinations are not available in public research.')
        try:
            addresses = {ipaddress.ip_address(host)}
        except ValueError:
            try:
                records = socket.getaddrinfo(host, port or (443 if parsed.scheme == 'https' else 80),
                                             type=socket.SOCK_STREAM)
            except OSError as exc:
                raise PolicyDenied('Destination hostname could not be safely resolved.') from exc
            addresses = {ipaddress.ip_address(record[4][0].split('%', 1)[0]) for record in records}
        if not addresses or any(not address.is_global for address in addresses):
            raise PolicyDenied('Local, private, reserved, and link-local destinations are blocked.')
        if self._scope_mode == 'selected_origins' and origin not in self._allowed_origins:
            raise PolicyDenied('This origin was not selected for the authenticated browser task.')
        return url

    def allows_action(self, action: str, *, target_kind: str | None) -> bool:
        if action in self._READ_ACTIONS:
            return True
        # Links can navigate to another public page after destination validation.
        if self._scope_mode == 'public_research':
            return action == 'click' and target_kind == 'page_link'
        if action not in self._capabilities:
            return False
        return action != 'click' or target_kind is not None
