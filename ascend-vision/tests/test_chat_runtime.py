import threading
import time
import sqlite3

from integrations.chat_ipc import ChatIpcQueue
from integrations.chat_runtime import ChatRuntimeBridge


def test_runtime_dispatches_typed_text_and_publishes_reply(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    message_id = queue.enqueue("When phone usage is observed, log Doomscrolling.")
    received = []
    handled = threading.Event()

    def handler(text):
        received.append(text)
        handled.set()
        return "Automation preview ready."

    bridge = ChatRuntimeBridge(queue, handler, poll_seconds=0.01)
    bridge.start()
    try:
        assert handled.wait(1.0)
        deadline = time.monotonic() + 1.0
        replies = queue.replies_after()
        while not replies and time.monotonic() < deadline:
            time.sleep(0.01)
            replies = queue.replies_after()
        assert received == ["When phone usage is observed, log Doomscrolling."]
        assert replies[-1]["message_id"] == message_id
        assert replies[-1]["status"] == "reply"
        assert replies[-1]["text"] == "Automation preview ready."
    finally:
        bridge.stop()


def test_runtime_converts_handler_failures_to_safe_errors(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    queue.enqueue("hello")
    handled = threading.Event()

    def handler(_text):
        handled.set()
        raise RuntimeError("private provider details")

    bridge = ChatRuntimeBridge(queue, handler, poll_seconds=0.01)
    bridge.start()
    try:
        assert handled.wait(1.0)
        deadline = time.monotonic() + 1.0
        while not queue.replies_after() and time.monotonic() < deadline:
            time.sleep(0.01)
        reply = queue.replies_after()[-1]
        assert reply["status"] == "error"
        assert reply["text"] == "Vision could not process that message right now."
        assert "private" not in reply["text"]
    finally:
        bridge.stop()


def test_runtime_does_not_report_success_when_handler_returns_no_answer(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    message_id = queue.enqueue("Can you answer me?")
    bridge = ChatRuntimeBridge(queue, lambda _text: None, poll_seconds=0.01)
    bridge.start()
    try:
        deadline = time.monotonic() + 1.0
        while not queue.replies_after() and time.monotonic() < deadline:
            time.sleep(0.01)
        replies = queue.replies_after()
        assert len(replies) == 1
        assert replies[0]["message_id"] == message_id
        assert replies[0]["status"] == "error"
        assert replies[0]["text"] == "Vision could not process that message right now."
    finally:
        bridge.stop()


def test_runtime_discards_in_flight_answer_after_stop(tmp_path):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    queue.enqueue("hello")
    entered = threading.Event()
    release = threading.Event()

    def handler(_text):
        entered.set()
        assert release.wait(4.0)
        return "Too late"

    bridge = ChatRuntimeBridge(queue, handler, poll_seconds=0.01)
    bridge.start()
    try:
        assert entered.wait(1.0)
        bridge.stop()
        release.set()
        assert bridge._thread is not None
        bridge._thread.join(timeout=1.0)
        assert not bridge._thread.is_alive()
        assert queue.replies_after() == []
    finally:
        release.set()
        bridge.stop()


def test_runtime_recovers_from_transient_queue_read_error(tmp_path, monkeypatch):
    queue = ChatIpcQueue(tmp_path / "chat.db")
    queue.enqueue("hello")
    original_receive = queue.receive_inbound
    failed_once = threading.Event()

    def flaky_receive():
        if not failed_once.is_set():
            failed_once.set()
            raise sqlite3.OperationalError("database is locked")
        return original_receive()

    monkeypatch.setattr(queue, "receive_inbound", flaky_receive)
    bridge = ChatRuntimeBridge(queue, lambda _text: "Recovered", poll_seconds=0.01)
    bridge.start()
    try:
        deadline = time.monotonic() + 1.0
        while not queue.replies_after() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert failed_once.is_set()
        assert queue.replies_after()[0]["text"] == "Recovered"
    finally:
        bridge.stop()
