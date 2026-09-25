"""Authenticated, read-only access to Ascend Core's status shelf."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re
from typing import Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_RESPONSE_BYTES = 256 * 1024
MAX_SNAPSHOT_AGE = timedelta(seconds=60)
SERVICE_TYPES = frozenset({"agent", "assistant", "bot", "vision", "provider"})
SERVICE_STATES = frozenset({"idle", "working", "stuck", "offline"})
IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class StatusShelfError(Exception):
    """The shelf cannot be trusted as a current source of status."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _open_without_redirect(request: Request, *, timeout: float):
    return build_opener(_NoRedirect).open(request, timeout=timeout)


@dataclass(frozen=True)
class ShelfService:
    service_id: str
    instance_id: str
    service_type: str
    state: Literal["idle", "working", "stuck", "offline"]
    state_since: datetime
    last_heartbeat_at: datetime | None
    stale_after_seconds: int
    activity_label: str | None = None


@dataclass(frozen=True)
class ShelfSnapshot:
    generated_at: datetime
    services: tuple[ShelfService, ...]


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid shelf timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Shelf timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _service(row: object, generated_at: datetime, now: datetime) -> ShelfService:
    if not isinstance(row, dict):
        raise ValueError("Invalid shelf service")
    service_id, instance_id = row.get("serviceId"), row.get("instanceId")
    if (not isinstance(service_id, str) or not IDENTIFIER.fullmatch(service_id)
            or not isinstance(instance_id, str) or not IDENTIFIER.fullmatch(instance_id)):
        raise ValueError("Invalid shelf service identity")
    service_type, state = row.get("serviceType"), row.get("state")
    if service_type not in SERVICE_TYPES or state not in SERVICE_STATES:
        raise ValueError("Invalid shelf service state")
    state_since = _aware_datetime(row.get("stateSince"))
    stale_after = row.get("staleAfterSeconds")
    if type(stale_after) is not int or not 1 <= stale_after <= 3600:
        raise ValueError("Invalid shelf stale threshold")
    heartbeat_value = row.get("lastHeartbeatAt")
    heartbeat = _aware_datetime(heartbeat_value) if heartbeat_value is not None else None
    if heartbeat is not None and heartbeat - generated_at > timedelta(seconds=5):
        raise ValueError("Shelf heartbeat is in the future")
    if state != "offline" and heartbeat is None:
        raise ValueError("Current shelf state has no heartbeat")
    stale_at_source = heartbeat is None or generated_at - heartbeat >= timedelta(seconds=stale_after)
    if state == "offline" and not stale_at_source:
        raise ValueError("Contradictory shelf state")
    if state != "offline" and heartbeat is not None and (
        stale_at_source or now - heartbeat >= timedelta(seconds=stale_after)
    ):
        state = "offline"
    activity = row.get("activity")
    if activity is not None and not isinstance(activity, dict):
        raise ValueError("Invalid shelf activity")
    label = activity.get("label") if activity else None
    if label is not None and (not isinstance(label, str) or len(label) > 160 or "\n" in label or "\r" in label):
        raise ValueError("Invalid shelf activity label")
    return ShelfService(service_id, instance_id, service_type, state, state_since,
                        heartbeat, stale_after, label)


class StatusShelfReader:
    """Fetch a small, fresh shelf using Core's dedicated reader credential."""

    def __init__(self, base_url: str, credential: str | None, *, timeout_seconds: float = 3.0,
                 opener: Callable = _open_without_redirect):
        if not isinstance(base_url, str):
            raise ValueError("Core URL must be a string")
        parts = urlsplit(base_url.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("Invalid Core URL")
        if parts.path not in {"", "/"} or parts.query or parts.fragment:
            raise ValueError("Core URL must be an origin")
        if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Remote Core URL must use HTTPS")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 10:
            raise ValueError("Status shelf timeout must be between 0 and 10 seconds")
        self._base_url = base_url.strip().rstrip("/")
        self._credential = credential.strip() if isinstance(credential, str) else None
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def read(self, *, now: datetime | None = None) -> ShelfSnapshot:
        if not self._credential:
            raise StatusShelfError("Status shelf credential is unavailable")
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must include a timezone")
        now = now.astimezone(timezone.utc)
        request = Request(
            self._base_url + "/api/status/shelf",
            headers={
                "Accept": "application/json",
                "Cache-Control": "no-store",
                "X-Status-Read-Credential": self._credential,
            },
            method="GET",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                if getattr(response, "status", None) != 200:
                    raise StatusShelfError("Status shelf returned a non-success response")
                body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise StatusShelfError("Status shelf response is too large")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict) or type(payload.get("schemaVersion")) is not int or payload["schemaVersion"] != 1:
                raise ValueError("Unsupported status shelf schema")
            generated_at = _aware_datetime(payload.get("generatedAt"))
            if abs(now - generated_at) > MAX_SNAPSHOT_AGE:
                raise ValueError("Status shelf snapshot is stale")
            rows = payload.get("services")
            if not isinstance(rows, list) or len(rows) > 100:
                raise ValueError("Invalid status shelf services")
            services = tuple(_service(row, generated_at, now) for row in rows)
            if len({(row.service_id, row.instance_id) for row in services}) != len(services):
                raise ValueError("Duplicate status shelf service")
            return ShelfSnapshot(generated_at, services)
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError,
                json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise StatusShelfError("Status shelf is unavailable or invalid") from exc
