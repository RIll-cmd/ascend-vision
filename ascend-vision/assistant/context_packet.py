"""Small, provenance-preserving projection of the current local laptop snapshot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import re

from assistant.context_runtime import ContextSnapshot, FIELD_SOURCES, FIELD_VALUES


MAX_CONTEXT_PACKET_BYTES = 4 * 1024
CONTEXT_LEASE_SECONDS = 30
_SOURCE_LABELS = {
    "webcam": "Webcam face detector",
    "desktop_activity": "Windows desktop activity sensor",
    "session_runtime": "Vision session manager",
    "user_declaration": "User declaration",
    "none": "No available source",
}
_CONTEXT_FIELD_SOURCES = {
    "deskPresence": {"webcam", "none"},
    "desktopActivity": {"desktop_activity", "none"},
    "foregroundCategory": {"desktop_activity", "none"},
    "focusSession": {"session_runtime", "none"},
    "declaredIntent": {"user_declaration", "none"},
}
_LAPTOP_CONTEXT_QUESTION = re.compile(
    r"\b(?:laptop|at\s+my\s+desk|my\s+desk|current\s+activity|what\s+am\s+i\s+doing|"
    r"taking\s+a\s+break|break\s+declaration|focus\s+session|screen\s+activity)\b", re.I,
)


def is_laptop_context_question(text: str) -> bool:
    return isinstance(text, str) and bool(_LAPTOP_CONTEXT_QUESTION.search(text))


def unavailable_context_read_response(reason: str = "laptop_offline", device_id: str = "laptop-unavailable") -> dict:
    if reason not in {"laptop_offline", "never_published"}:
        reason = "laptop_offline"
    return {
        "available": False,
        "reason": reason,
        "deviceId": device_id,
        "lastSeenAt": None,
        "generatedAt": None,
        "snapshotId": None,
        "fields": {
            name: {
                "value": value, "source": "none", "freshness": "unavailable",
                "observedAt": None, "expiresAt": None, "ageSeconds": None,
                "evidenceKind": "user_report" if name == "declaredIntent" else "observation",
            }
            for name, value in {
                "deskPresence": "unknown", "desktopActivity": "unavailable",
                "foregroundCategory": "unknown", "focusSession": "unavailable",
                "declaredIntent": "none",
            }.items()
        },
    }


def validate_context_read_response(packet: object) -> dict:
    """Accept only Core's bounded, typed snapshot projection before it reaches chat."""
    if not isinstance(packet, dict) or type(packet.get("available")) is not bool:
        raise ValueError("laptop context response is invalid")
    allowed_keys = {"available", "reason", "deviceId", "bootId", "snapshotId",
                    "generatedAt", "lastSeenAt", "leaseExpiresAt", "fields"}
    if set(packet) - allowed_keys:
        raise ValueError("laptop context response is invalid")
    for name in ("deviceId", "bootId", "snapshotId"):
        value = packet.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > 160):
            raise ValueError("laptop context identity is invalid")
    for name in ("generatedAt", "lastSeenAt", "leaseExpiresAt"):
        value = packet.get(name)
        if value is not None and (not isinstance(value, str) or len(value) > 40):
            raise ValueError("laptop context timestamp is invalid")
    if packet.get("reason") not in {"current", "laptop_offline", "never_published"}:
        raise ValueError("laptop context response is invalid")
    fields = packet.get("fields")
    if not isinstance(fields, dict) or set(fields) != set(_CONTEXT_FIELD_SOURCES):
        raise ValueError("laptop context response is invalid")
    valid_values = {
        "deskPresence": {"present", "away", "unknown"},
        "desktopActivity": {"input_active", "input_idle", "locked", "unavailable"},
        "foregroundCategory": {"development", "communication", "browser_unspecified", "entertainment", "other", "unknown"},
        "focusSession": {"focus", "background", "unavailable"},
        "declaredIntent": {"focus", "break", "research", "meeting", "none"},
    }
    result = dict(packet)
    clean_fields = {}
    for name, field in fields.items():
        if (not isinstance(field, dict) or set(field) != {
            "value", "source", "freshness", "observedAt", "expiresAt", "ageSeconds", "evidenceKind",
        }):
            raise ValueError("laptop context field is invalid")
        if (not isinstance(field["value"], str) or not isinstance(field["source"], str)
                or not isinstance(field["freshness"], str) or not isinstance(field["evidenceKind"], str)
                or field["value"] not in valid_values[name]
                or field["source"] not in _CONTEXT_FIELD_SOURCES[name]
                or field["freshness"] not in {"fresh", "stale", "unavailable", "paused"}
                or field["evidenceKind"] not in {"observation", "user_report", "inference"}):
            raise ValueError("laptop context field is invalid")
        age = field["ageSeconds"]
        if age is not None and (type(age) is not int or age < 0):
            raise ValueError("laptop context age is invalid")
        for timestamp_name in ("observedAt", "expiresAt"):
            value = field[timestamp_name]
            if value is not None and (not isinstance(value, str) or len(value) > 40):
                raise ValueError("laptop context timestamp is invalid")
        if field["freshness"] == "fresh" and (
            field["source"] == "none" or field["observedAt"] is None or field["expiresAt"] is None
        ):
            raise ValueError("laptop context freshness is invalid")
        clean_fields[name] = dict(field)
    result["fields"] = clean_fields
    return result


def build_context_packet(snapshot: ContextSnapshot, *, now: datetime | None = None) -> dict:
    if not isinstance(snapshot, ContextSnapshot):
        raise TypeError("snapshot must be a ContextSnapshot")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must include a timezone")
    current = current.astimezone(timezone.utc)
    fields = {}
    for name, field in snapshot.fields.items():
        if name not in FIELD_VALUES or field.value not in FIELD_VALUES[name]:
            continue
        if field.source not in FIELD_SOURCES.get(name, frozenset()) | {"none"}:
            continue
        if field.freshness not in {"fresh", "stale", "unavailable", "paused"}:
            continue
        age = None
        observed_at = None
        if field.observed_at is not None:
            observed = field.observed_at.astimezone(timezone.utc)
            age_value = (current - observed).total_seconds()
            if age_value >= 0:
                age = int(age_value)
                observed_at = observed.isoformat()
        fields[name] = {
            "value": field.value,
            "source": field.source,
            "freshness": field.freshness,
            "observedAt": observed_at,
            "expiresAt": (field.expires_at.astimezone(timezone.utc).isoformat()
                          if field.expires_at is not None else None),
            "ageSeconds": age,
            "evidenceKind": field.evidence_kind,
        }
    packet = {
        "schemaVersion": snapshot.schema_version,
        "source": "local Vision laptop context",
        "deviceId": snapshot.device_id,
        "bootId": snapshot.boot_id,
        "bootStartedAt": snapshot.boot_started_at.astimezone(timezone.utc).isoformat(),
        "snapshotRevision": snapshot.revision,
        "sourceSequences": dict(snapshot.source_sequences),
        "snapshotId": snapshot.snapshot_id,
        "generatedAt": snapshot.generated_at.astimezone(timezone.utc).isoformat(),
        "leaseExpiresAt": (snapshot.generated_at + timedelta(seconds=CONTEXT_LEASE_SECONDS))
        .astimezone(timezone.utc).isoformat(),
        "paused": snapshot.paused,
        "fields": fields,
    }
    encoded = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_CONTEXT_PACKET_BYTES:
        raise ValueError("laptop context packet exceeds the 4 KiB limit")
    return packet


def _age_text(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "age unavailable"
    if age_seconds < 60:
        return f"{age_seconds} seconds ago"
    return f"{age_seconds // 60} minutes ago"


def _field_text(packet: dict, name: str, labels: dict[str, str]) -> str:
    field = packet["fields"].get(name)
    if field is None:
        return "unavailable"
    if field["freshness"] != "fresh":
        return f"unknown ({field['freshness']}, last evidence {_age_text(field.get('ageSeconds'))})"
    value = labels.get(field["value"], "unknown")
    source = _SOURCE_LABELS.get(field["source"], "unknown source")
    return f"{value} ({source}, {_age_text(field['ageSeconds'])})"


def render_activity_answer(packet: dict) -> str:
    presence = _field_text(packet, "deskPresence", {
        "present": "a face is detected in the webcam frame",
        "away": "no face has been detected in the webcam frame after the absence dwell",
        "unknown": "webcam presence is unknown",
    })
    activity = _field_text(packet, "desktopActivity", {
        "input_active": "recent keyboard/mouse activity", "input_idle": "no recent keyboard/mouse activity",
        "locked": "desktop locked", "unavailable": "desktop activity unavailable",
    })
    category = _field_text(packet, "foregroundCategory", {
        "development": "a development app is open", "communication": "a communication app is open",
        "browser_unspecified": "a browser is open; its content is unknown",
        "entertainment": "an entertainment app is open", "other": "another app is open", "unknown": "unknown app category",
    })
    session = _field_text(packet, "focusSession", {
        "focus": "Vision session mode is Focus", "background": "Vision session mode is Background",
        "unavailable": "Vision session mode unavailable",
    })
    return (f"Current local signals: {presence}; {activity}; {category}; {session}. "
            "An open development app does not confirm coding progress.")


def render_break_answer(packet: dict) -> str:
    labels = {
        "break": "a break", "focus": "focus", "research": "research", "meeting": "a meeting", "none": "no intent",
    }
    field = packet["fields"].get("declaredIntent", {})
    if field.get("freshness") != "fresh":
        return "I don't have a current declared break or focus intent. Laptop activity cannot tell me whether you're taking a break."
    intent = labels.get(field.get("value"), "an unknown intent")
    age = _age_text(field.get("ageSeconds"))
    if field.get("value") == "break":
        return f"You declared {intent} {age}. This is your declaration, not a break detected from activity."
    return f"Your current declared intent is {intent} ({age}); I don't have a break declaration."
