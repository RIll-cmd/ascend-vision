"""Local dashboard chat IPC stays bounded, ordered, and single-consumer."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3

import pytest

from integrations.chat_ipc import ChatIpcQueue


def test_concurrent_first_time_constructors_initialize_without_lock_errors(tmp_path):
    for index in range(12):
        path = tmp_path / f"chat-{index}.db"
        with ThreadPoolExecutor(max_workers=12) as pool:
            queues = list(pool.map(lambda _: ChatIpcQueue(path), range(12)))
        assert all(queue.path == path for queue in queues)


@pytest.mark.parametrize("value", [True, False, 1.5, "5", None, 0, -1])
@pytest.mark.parametrize("option", ["max_text_length", "retention_limit"])
def test_constructor_requires_positive_non_boolean_integer_bounds(tmp_path, option, value):
    with pytest.raises(ValueError):
        ChatIpcQueue(tmp_path / "chat.db", **{option: value})


def test_enqueue_validates_and_preserves_bounded_text(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db", max_text_length=5)

    message_id = queue.enqueue("it's", source="dashboard")

    inbound = queue.receive_inbound()
    assert inbound["message_id"] == message_id
    assert inbound["text"] == "it's"
    assert inbound["source"] == "dashboard"
    assert inbound["created_at"].endswith("Z")


@pytest.mark.parametrize("text", [None, 7, "", "   ", "123456"])
def test_enqueue_rejects_malformed_text(tmp_path, text):
    queue = ChatIpcQueue(tmp_path / "chat.db", max_text_length=5)

    with pytest.raises(ValueError):
        queue.enqueue(text)


def test_receive_inbound_claims_each_message_once_across_queue_instances(tmp_path):
    path = tmp_path / "chat.db"
    producer = ChatIpcQueue(path)
    message_ids = [producer.enqueue(f"message {index}") for index in range(8)]
    consumers = [ChatIpcQueue(path) for _ in message_ids]

    with ThreadPoolExecutor(max_workers=len(consumers)) as pool:
        claimed = list(pool.map(lambda queue: queue.receive_inbound(), consumers))

    assert {message["message_id"] for message in claimed} == set(message_ids)
    assert all(queue.receive_inbound() is None for queue in consumers)


def test_replies_after_returns_safe_replies_in_cursor_order(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    first_message_id = queue.enqueue("first")
    second_message_id = queue.enqueue("second")

    first_cursor = queue.reply(first_message_id, "waiting", "queued")
    second_cursor = queue.reply(second_message_id, "done", "reply")

    assert second_cursor > first_cursor
    assert queue.replies_after(first_cursor) == [{
        "cursor": second_cursor,
        "message_id": second_message_id,
        "text": "done",
        "status": "reply",
        "created_at": queue.replies_after(0)[1]["created_at"],
    }]


@pytest.mark.parametrize(
    ("message_id", "text", "status"),
    [
        ("", "ok", "reply"),
        ("missing", "", "reply"),
        ("missing", "123456", "reply"),
        ("missing", "ok", "unknown"),
    ],
)
def test_reply_rejects_malformed_input(tmp_path, message_id, text, status):
    queue = ChatIpcQueue(tmp_path / "chat.db", max_text_length=5)

    with pytest.raises(ValueError):
        queue.reply(message_id, text, status)


def test_queue_uses_wal_and_retains_only_the_newest_completed_rows(tmp_path):
    path = tmp_path / "chat.db"
    queue = ChatIpcQueue(path, retention_limit=2)
    message_ids = []
    for index in range(3):
        message_ids.append(queue.enqueue(f"message {index}"))
        assert queue.receive_inbound()["message_id"] == message_ids[-1]
        queue.reply(message_ids[-1], f"reply {index}", "reply")

    with sqlite3.connect(path) as connection:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        inbound_ids = [row[0] for row in connection.execute(
            "SELECT message_id FROM chat_inbox ORDER BY sequence"
        )]

    assert journal_mode == "wal"
    assert inbound_ids == message_ids[-2:]
    assert [reply["message_id"] for reply in queue.replies_after()] == message_ids[-2:]


def test_retention_preserves_claimed_messages_until_a_terminal_reply(tmp_path):
    path = tmp_path / "chat.db"
    queue = ChatIpcQueue(path, retention_limit=1)
    first_id = queue.enqueue("first")
    assert queue.receive_inbound()["message_id"] == first_id
    second_id = queue.enqueue("second")
    assert queue.receive_inbound()["message_id"] == second_id
    queue.reply(second_id, "still waiting", "queued")
    third_id = queue.enqueue("third")

    with sqlite3.connect(path) as connection:
        before_terminal_replies = [row[0] for row in connection.execute(
            "SELECT message_id FROM chat_inbox ORDER BY sequence"
        )]
    assert before_terminal_replies == [first_id, second_id, third_id]

    queue.reply(first_id, "first done", "reply")
    queue.reply(second_id, "second done", "reply")

    with sqlite3.connect(path) as connection:
        retained_ids = [row[0] for row in connection.execute(
            "SELECT message_id FROM chat_inbox ORDER BY sequence"
        )]
    assert retained_ids == [second_id, third_id]


def test_acknowledgement_removes_delivered_inbox_and_outbox_text(tmp_path):
    path = tmp_path / "chat.db"
    queue = ChatIpcQueue(path)
    message_id = queue.enqueue("temporary transcript")
    queue.receive_inbound()
    cursor = queue.reply(message_id, "temporary answer", "reply")
    queue.replies_after()

    queue.acknowledge_through(cursor)

    assert queue.replies_after() == []
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM chat_inbox").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM chat_outbox").fetchone()[0] == 0


def test_acknowledgement_rejects_cursor_beyond_delivered_replies(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    message_id = queue.enqueue("temporary transcript")
    queue.receive_inbound()
    cursor = queue.reply(message_id, "temporary answer", "reply")

    with pytest.raises(ValueError):
        queue.acknowledge_through(cursor + 1)

    assert [reply["cursor"] for reply in queue.replies_after()] == [cursor]


def test_acknowledgement_does_not_remove_unfinished_or_newer_messages(tmp_path):
    path = tmp_path / "chat.db"
    queue = ChatIpcQueue(path)
    finished_id = queue.enqueue("finished")
    pending_id = queue.enqueue("pending")
    queue.receive_inbound()
    queue.receive_inbound()
    old_cursor = queue.reply(finished_id, "done", "reply")
    newer_cursor = queue.reply(pending_id, "waiting", "queued")
    queue.replies_after()

    queue.acknowledge_through(old_cursor)

    assert queue.replies_after()[0]["cursor"] == newer_cursor
    with sqlite3.connect(path) as db:
        assert [row[0] for row in db.execute("SELECT message_id FROM chat_inbox")] == [pending_id]


def test_older_than_one_day_chat_rows_expire_but_fresh_pending_survives(tmp_path):
    path = tmp_path / "chat.db"
    queue = ChatIpcQueue(path)
    old_id = queue.enqueue("old private text")
    fresh_id = queue.enqueue("new message")
    with sqlite3.connect(path) as db:
        db.execute("UPDATE chat_inbox SET created_at='2020-01-01T00:00:00.000Z' WHERE message_id=?", (old_id,))

    inbound = queue.receive_inbound()

    assert inbound["message_id"] == fresh_id
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM chat_inbox WHERE message_id=?", (old_id,)).fetchone()[0] == 0


def test_session_events_are_independent_reads_and_clear_when_session_ends(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    queue.begin_session("runtime-one")

    turn_id = queue.enqueue("hello from Fairy", source="fairy")
    inbound = queue.receive_inbound()
    assert inbound["session_id"] == "runtime-one"
    queue.publish_event("runtime-one", turn_id=turn_id, source="fairy",
                        kind="user", text="hello from Fairy", status="received")
    queue.publish_event("runtime-one", turn_id=turn_id, source="fairy",
                        kind="assistant", text="Hello back.", status="reply")

    first_reader = queue.events_after("runtime-one", 0)
    second_reader = queue.events_after("runtime-one", 0)
    assert [event["text"] for event in first_reader] == ["hello from Fairy", "Hello back."]
    assert second_reader == first_reader

    queue.end_session("runtime-one")
    assert queue.active_session_id() is None
    assert queue.events_after("runtime-one", 0) == []
    assert queue.receive_inbound() is None


def test_chat_events_keep_reply_provenance_separate_from_input_channel(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    queue.begin_session("runtime-one")
    queue.publish_event("runtime-one", turn_id="turn-1", source="fairy", kind="assistant",
                        text="A real reply", status="reply", reply_source="model",
                        provider="gemini", model="gemini-test")

    event = queue.events_after("runtime-one")[0]
    assert event["source"] == "fairy"
    assert event["reply_source"] == "model"
    assert event["provider"] == "gemini"
    assert event["model"] == "gemini-test"

    with pytest.raises(ValueError, match="provenance"):
        queue.publish_event("runtime-one", turn_id="turn-2", source="fairy", kind="user",
                            text="secret", reply_source="model")


def test_legacy_chat_event_table_is_migrated_additively(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE chat_events (sequence INTEGER PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL, source TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL)")
        db.execute("INSERT INTO chat_events VALUES (1,'runtime-old','turn-old','voice','assistant','old','reply','2026-01-01T00:00:00Z')")

    queue = ChatIpcQueue(path)

    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(chat_events)")}
    assert {"reply_source", "provider", "model", "failure_reason"} <= columns
    assert queue.events_after("runtime-old")[0]["reply_source"] is None


def test_ai_status_starts_unknown_and_session_restart_clears_stale_success(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    assert queue.ai_status()["state"] == "unknown"
    queue.begin_session("runtime-one")
    queue.publish_ai_status({"configured": True, "state": "available", "provider": "gemini",
                             "model": "gemini-test", "lastSuccessAt": "2026-09-30T00:00:00Z"})
    assert queue.ai_status()["state"] == "available"
    queue.end_session("runtime-one")
    assert queue.ai_status()["state"] == "unknown"


def test_session_scoped_enqueue_rejects_restart_and_draining_races(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    queue.begin_session("runtime-one")

    with pytest.raises(RuntimeError, match="not accepting"):
        queue.enqueue("stale session", source="fairy", session_id="runtime-old")

    queue.stop_accepting_session("runtime-one")
    with pytest.raises(RuntimeError, match="not accepting"):
        queue.enqueue("late dashboard message", source="dashboard", session_id="runtime-one")
    assert queue.receive_inbound() is None
    queue.end_session("runtime-one")
