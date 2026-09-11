import threading
import time

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
