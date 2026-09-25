from config import FeedbackConfig
from assistant.service import AssistantService


class RecordingGenerator:
    def __init__(self):
        self.payloads = []

    def generate_chat(self, text, context, max_words=25):
        self.payloads.append(context.payload() if context is not None else {})
        return f"Reply to {text}."


def test_phone_session_context_isolated_from_other_phone_sessions_and_local_chat():
    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator)
    phone_a = ("owner-a", "phone_pwa", "session-a")
    phone_b = ("owner-a", "phone_pwa", "session-b")
    other_owner = ("owner-b", "phone_pwa", "session-a")

    assistant.respond("Remember this first turn", session_key=phone_a)
    assistant.respond("Continue my first chat", session_key=phone_a)
    assert generator.payloads[-1]["recent_turns"] == [
        {"user": "Remember this first turn", "assistant": "Reply to Remember this first turn."}
    ]

    assistant.respond("Start a separate chat", session_key=phone_b)
    assert "recent_turns" not in generator.payloads[-1]

    assistant.respond("Different owner, same session label", session_key=other_owner)
    assert "recent_turns" not in generator.payloads[-1]

    assistant.respond("Local voice or dashboard chat")
    assert "Remember this first turn" not in str(generator.payloads[-1])

    assistant.clear_session(phone_a)
    assistant.respond("Continue after New Chat", session_key=phone_a)
    assert "recent_turns" not in generator.payloads[-1]

