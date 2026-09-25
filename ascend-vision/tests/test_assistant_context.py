import sqlite3
import time

from assistant.context import build_conversation_context


def test_context_reads_only_the_current_sessions_event_counts(tmp_path):
    path = tmp_path / "events.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE phone_events (session_id INTEGER, event_type TEXT)")
        connection.executemany(
            "INSERT INTO phone_events (session_id, event_type) VALUES (?, ?)",
            [(7, "phone_held"), (7, "phone_held"), (7, "slouch"),
             (7, "yawn"), (8, "phone_held")],
        )

    context = build_conversation_context(
        "How am I doing?", session_id=7, mode="focus",
        database_path=path, started_at=time.perf_counter() - 30,
    )

    assert context.user_query == "How am I doing?"
    assert context.mode == "focus"
    assert context.phone_pickups == 2
    assert context.slouch_events == 1
    assert context.yawns == 1
    assert context.microsleep_events == 0
    assert 0.4 <= context.session_duration_minutes <= 0.6


def test_context_falls_back_to_zero_counts_without_creating_missing_database(tmp_path):
    path = tmp_path / "missing.db"

    context = build_conversation_context(
        "Hello", session_id=7, mode="background",
        database_path=path, started_at=None,
    )

    assert context.phone_pickups == 0
    assert context.slouch_events == 0
    assert context.session_duration_minutes == 0
    assert not path.exists()
