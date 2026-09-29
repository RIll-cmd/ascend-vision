import json

from config import FeedbackConfig
from assistant.service import AssistantService
from assistant.session_store import SessionContextStore


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


def test_session_context_store_expires_idle_session_after_sixty_minutes():
    store = SessionContextStore()
    key = ("owner-a", "phone_pwa", "session-1")
    store.record(key, "first turn", "first answer", now=100.0)

    turns, expired = store.get(key, now=3_699.0)
    assert turns == (("first turn", "first answer"),)
    assert expired is False

    turns, expired = store.get(key, now=3_700.0)
    assert turns == ()
    assert expired is True


def test_session_context_store_bounds_turn_count_and_serialized_bytes():
    store = SessionContextStore()
    key = ("owner-a", "phone_pwa", "session-1")
    for index in range(14):
        store.record(key, f"question {index} " + "q" * 200, "answer " + "a" * 100, now=index)

    turns, expired = store.get(key, now=20.0)
    encoded = json.dumps(
        [{"user": user, "assistant": answer} for user, answer in turns],
        ensure_ascii=False,
    ).encode("utf-8")
    assert expired is False
    assert len(turns) <= 12
    assert all("question 0 " not in user for user, _ in turns)
    assert len(encoded) <= 3_000


def test_session_context_store_clears_only_the_requested_session():
    store = SessionContextStore()
    session_a = ("owner-a", "phone_pwa", "session-a")
    session_b = ("owner-a", "phone_pwa", "session-b")
    store.record(session_a, "A question", "A answer", now=10.0)
    store.record(session_b, "B question", "B answer", now=10.0)

    store.clear(session_a)

    assert store.get(session_a, now=11.0) == ((), False)
    assert store.get(session_b, now=11.0) == ((("B question", "B answer"),), False)
