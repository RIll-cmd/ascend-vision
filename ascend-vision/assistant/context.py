"""Build small chat context without sharing the runtime's SQLite connection."""
from __future__ import annotations

from contextlib import closing
import logging
from pathlib import Path
import sqlite3
import time

from feedback import ConversationContext


LOG = logging.getLogger(__name__)


def build_conversation_context(
    user_text: str,
    *,
    session_id: int | None,
    mode: str,
    database_path: str | Path,
    started_at: float | None,
) -> ConversationContext:
    """Read only the active session's event counts with a separate connection."""
    counts: dict[str, int] = {}
    if session_id is not None:
        path = Path(database_path).resolve()
        try:
            with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.5)) as connection:
                rows = connection.execute(
                    "SELECT event_type, COUNT(*) FROM phone_events WHERE session_id=? GROUP BY event_type",
                    (session_id,),
                ).fetchall()
                counts = {event_type: count for event_type, count in rows}
        except (OSError, sqlite3.Error) as exc:
            LOG.debug("Chat session counts unavailable (%s)", type(exc).__name__)

    minutes = max(0.0, time.perf_counter() - started_at) / 60.0 if started_at is not None else 0.0
    return ConversationContext(
        user_query=user_text,
        mode=mode,
        phone_pickups=counts.get("phone_held", 0),
        microsleep_events=counts.get("drowsiness_microsleep", 0),
        yawns=counts.get("yawn", 0),
        slouch_events=counts.get("slouch", 0),
        session_duration_minutes=minutes,
    )
