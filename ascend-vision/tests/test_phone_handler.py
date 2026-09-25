import importlib
import importlib.util

import pytest

from assistant.service import AssistantReply


class RecordingAssistant:
    def __init__(self):
        self.calls = []

    def respond(self, text, context=None, *, max_words=25, session_key=None):
        self.calls.append((text, context, max_words, session_key))
        return AssistantReply("The assistant's typed reply.", "model")

    def clear_session(self, session_key):
        self.calls.append(("clear", session_key))


def _handler_class():
    if importlib.util.find_spec("assistant.phone_handler") is None:
        pytest.fail("PhoneMessageHandler module has not been implemented")
    return importlib.import_module("assistant.phone_handler").PhoneMessageHandler


def test_phone_handler_routes_message_with_owner_channel_and_session_scope():
    assistant = RecordingAssistant()
    handler = _handler_class()(assistant, owner_id="owner-a")

    reply = handler.handle("owner-a", "phone_pwa", "session-1", "What is Hub doing?")

    assert reply == AssistantReply("The assistant's typed reply.", "model")
    assert assistant.calls == [
        ("What is Hub doing?", None, 25, ("owner-a", "phone_pwa", "session-1"))
    ]


@pytest.mark.parametrize(
    "owner_id,channel,session_id,text",
    [
        ("owner-b", "phone_pwa", "session-1", "Hello"),
        ("owner-a", "unknown", "session-1", "Hello"),
        ("owner-a", "phone_pwa", "not a valid session id!", "Hello"),
        ("owner-a", "phone_pwa", "session-1", " "),
        ("owner-a", "phone_pwa", "session-1", "x" * 4001),
    ],
)
def test_phone_handler_denies_wrong_owner_or_invalid_request_before_assistant_call(
    owner_id, channel, session_id, text
):
    assistant = RecordingAssistant()
    handler = _handler_class()(assistant, owner_id="owner-a")

    with pytest.raises(ValueError):
        handler.handle(owner_id, channel, session_id, text)

    assert assistant.calls == []


@pytest.mark.parametrize(
    "text",
    [
        "Forget green tea",
        "Correct that memory: I prefer black coffee",
        "Approve memory 12",
        "Do not remember this conversation",
    ],
)
def test_phone_handler_blocks_memory_administration_without_calling_assistant(text):
    assistant = RecordingAssistant()
    handler = _handler_class()(assistant, owner_id="owner-a")

    reply = handler.handle("owner-a", "phone_pwa", "session-1", text)

    assert "dashboard" in reply.text.lower()
    assert assistant.calls == []

