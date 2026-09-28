"""Read-only, owner-scoped Ascend Core missions query using the Vision token."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request
from uuid import uuid4

from integrations.status_shelf import _open_without_redirect


VISION_CONTRACT_VERSION = "2026-09-09"
MAX_MISSION_RESPONSE_BYTES = 32 * 1024
MAX_MISSION_COMPLETIONS = 100
_MISSION_STATUSES = frozenset({"PENDING", "COMPLETED", "MISSED"})


@dataclass(frozen=True)
class MissionSummary:
    name: str
    status: str


@dataclass(frozen=True)
class MissionCompletion:
    mission_id: str
    name: str
    completed_at: datetime
    completion_type: str | None


@dataclass(frozen=True)
class MissionSnapshot:
    retrieved_at: datetime
    missions: tuple[MissionSummary, ...] = field(repr=True)


def _safe_missions(value: object) -> tuple[MissionSummary, ...]:
    if not isinstance(value, list) or len(value) > 50:
        raise ValueError("Invalid Core missions payload")
    result = []
    for row in value:
        if not isinstance(row, dict):
            raise ValueError("Invalid Core mission row")
        name, status = row.get("name"), row.get("status")
        if (not isinstance(name, str) or not name.strip() or len(name) > 160
                or any(ord(ch) < 32 for ch in name)
                or not isinstance(status, str) or status.upper() not in _MISSION_STATUSES):
            raise ValueError("Invalid Core mission summary")
        result.append(MissionSummary(name.strip(), status.upper()))
    return tuple(result)


def render_missions_answer(snapshot: MissionSnapshot, *, now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    age = max(0, int((current.astimezone(timezone.utc) - snapshot.retrieved_at).total_seconds()))
    age_text = f"{age} seconds ago" if age < 60 else f"{age // 60} minutes ago"
    if not snapshot.missions:
        answer = "Ascend Core reports no missions scheduled for today."
    else:
        entries = [f"{mission.name} ({mission.status.lower().replace('_', ' ')})"
                   for mission in snapshot.missions[:6]]
        answer = "Today's missions from Ascend Core: " + "; ".join(entries) + "."
        if len(snapshot.missions) > len(entries):
            answer += f" Plus {len(snapshot.missions) - len(entries)} more."
    return f"{answer} Source: Ascend Core, retrieved {age_text}."


class VisionMissionReader:
    """Fetch today's mission summaries without exposing any Core write operation."""

    def __init__(self, base_url: str, token_provider: Callable[[], str | None],
                 character_provider: Callable[[], str | None], *, timeout_seconds: float = 3.0,
                 opener: Callable = _open_without_redirect, clock=lambda: datetime.now(timezone.utc)):
        parts = urlsplit(base_url.strip() if isinstance(base_url, str) else "")
        if (parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password
                or parts.path not in {"", "/"} or parts.query or parts.fragment
                or parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("Invalid Core URL")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 10:
            raise ValueError("Mission query timeout must be between 0 and 10 seconds")
        if not callable(token_provider) or not callable(character_provider):
            raise TypeError("Mission query credential providers must be callable")
        self._base_url = base_url.strip().rstrip("/")
        self._token_provider = token_provider
        self._character_provider = character_provider
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener
        self._clock = clock

    def read_missions(self) -> MissionSnapshot:
        token = self._token_provider()
        character_id = self._character_provider()
        if not isinstance(token, str) or not token.strip() or not isinstance(character_id, str) or not character_id.strip():
            raise RuntimeError("Core authorization is unavailable")
        request_id = f"vision-query-{uuid4().hex[:16]}"
        payload = {
            "characterId": character_id.strip(),
            "requestId": request_id,
            "capabilityVersion": VISION_CONTRACT_VERSION,
            "intent": "missions_summary",
            "parameters": {},
        }
        request = Request(
            self._base_url + "/api/integration/vision/query",
            data=json.dumps(payload, allow_nan=False).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Cache-Control": "no-store", "Authorization": f"Bearer {token.strip()}"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                if getattr(response, "status", None) != 200:
                    raise ValueError("Core mission query returned a non-success response")
                body = response.read(MAX_MISSION_RESPONSE_BYTES + 1)
            if len(body) > MAX_MISSION_RESPONSE_BYTES:
                raise ValueError("Core mission response is too large")
            result = json.loads(body.decode("utf-8"))
            if (not isinstance(result, dict) or result.get("success") is not True
                    or result.get("requestId") != request_id
                    or result.get("intent") != "missions_summary"
                    or not isinstance(result.get("data"), dict)):
                raise ValueError("Core mission response does not match its request")
            missions = _safe_missions(result["data"].get("missions"))
            retrieved_at = self._clock()
            if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
                raise ValueError("Mission reader clock must include a timezone")
            return MissionSnapshot(retrieved_at.astimezone(timezone.utc), missions)
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("Ascend Core missions are unavailable") from exc

    def read_completion_history(self, start_at: datetime, end_at: datetime) -> tuple[MissionCompletion, ...]:
        """Read Core-confirmed completions for one bounded timezone-aware window."""
        for value in (start_at, end_at):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("mission history boundaries must include timezone offsets")
        start_utc, end_utc = start_at.astimezone(timezone.utc), end_at.astimezone(timezone.utc)
        if end_utc <= start_utc or end_utc - start_utc > timedelta(days=31):
            raise ValueError("mission history window must be at most 31 days")
        token = self._token_provider()
        character_id = self._character_provider()
        if not isinstance(token, str) or not token.strip() or not isinstance(character_id, str) or not character_id.strip():
            raise RuntimeError("Core authorization is unavailable")
        request_id = f"vision-query-{uuid4().hex[:16]}"
        payload = {
            "characterId": character_id.strip(), "requestId": request_id,
            "capabilityVersion": VISION_CONTRACT_VERSION,
            "intent": "mission_completion_history",
            "parameters": {"startAt": start_utc.isoformat(), "endAt": end_utc.isoformat()},
        }
        request = Request(
            self._base_url + "/api/integration/vision/query",
            data=json.dumps(payload, allow_nan=False).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json",
                     "Cache-Control": "no-store", "Authorization": f"Bearer {token.strip()}"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                if getattr(response, "status", None) != 200:
                    raise ValueError("Core mission history query returned a non-success response")
                body = response.read(MAX_MISSION_RESPONSE_BYTES + 1)
            if len(body) > MAX_MISSION_RESPONSE_BYTES:
                raise ValueError("Core mission history response is too large")
            result = json.loads(body.decode("utf-8"))
            if (not isinstance(result, dict) or result.get("success") is not True
                    or result.get("requestId") != request_id
                    or result.get("intent") != "mission_completion_history"
                    or not isinstance(result.get("data"), dict)):
                raise ValueError("Core response did not match the mission history request")
            rows = result["data"].get("completions")
            if not isinstance(rows, list) or len(rows) > MAX_MISSION_COMPLETIONS:
                raise ValueError("Core mission completion list is invalid")
            completions = []
            seen = set()
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("Invalid Core mission completion")
                mission_id, name, completed = row.get("id"), row.get("name"), row.get("completedAt")
                kind = row.get("completionType")
                if (not isinstance(mission_id, str) or not mission_id.strip() or len(mission_id) > 128
                        or mission_id in seen or not isinstance(name, str) or not name.strip() or len(name) > 160
                        or not isinstance(completed, str)
                        or kind is not None and kind not in {"MINI", "NORMAL", "ELITE", "HARDCORE"}):
                    raise ValueError("Invalid Core mission completion")
                completed_at = datetime.fromisoformat(completed.replace("Z", "+00:00"))
                if completed_at.tzinfo is None or completed_at.utcoffset() is None:
                    raise ValueError("Core mission completion timestamp must include a timezone")
                completed_at = completed_at.astimezone(timezone.utc)
                if not start_utc <= completed_at < end_utc:
                    raise ValueError("Core mission completion is outside the requested window")
                seen.add(mission_id)
                completions.append(MissionCompletion(mission_id, name.strip(), completed_at, kind))
            return tuple(completions)
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError,
                json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Ascend Core mission history is unavailable") from exc
