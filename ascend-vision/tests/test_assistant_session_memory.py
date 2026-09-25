import json

from assistant.memory import MemoryStore
from assistant.service import AssistantService
from config import FeedbackConfig
from feedback import ConversationContext


class RecordingGenerator:
    def __init__(self, reply="A useful answer."):
        self.reply = reply
        self.payloads = []

    def generate_chat(self, text, context, max_words=25):
        self.payloads.append(context.payload() if context is not None else {})
        return self.reply


def test_recent_turns_are_process_local(tmp_path):
    generator = RecordingGenerator()
    first = AssistantService(FeedbackConfig(), generator=generator)

    first.respond("hello")
    first.respond("and now?")

    assert generator.payloads[-1]["recent_turns"] == [{"user": "hello", "assistant": "A useful answer."}]

    fresh = AssistantService(FeedbackConfig(), generator=generator)
    fresh.respond("and now?")
    assert "recent_turns" not in generator.payloads[-1]


def test_only_relevant_approved_memory_enters_model_context(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.approve(store.propose("I prefer green tea"))
    store.approve(store.propose("I play tennis"))
    store.propose("I like mango")
    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator, memory_store=store)

    assistant.respond("What green tea should I try?")

    facts = generator.payloads[-1]["approved_memories"]
    assert [row["text"] for row in facts] == ["I prefer green tea"]
    assert all(isinstance(row["id"], int) for row in facts)
    assert "mango" not in str(generator.payloads[-1])


def test_recent_window_drops_old_turns_and_respects_byte_budget(tmp_path):
    generator = RecordingGenerator(reply="okay")
    assistant = AssistantService(FeedbackConfig(), generator=generator)
    for index in range(13):
        assistant.respond(f"turn {index} " + "x" * 250)
    assistant.respond("next")

    turns = generator.payloads[-1]["recent_turns"]
    assert len(turns) <= 12
    assert all(not turn["user"].startswith("turn 0 ") for turn in turns)
    assert len(json.dumps(turns).encode("utf-8")) <= 3000


def test_remember_command_creates_only_a_proposal(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator, memory_store=store)

    answer = assistant.respond("Remember that I prefer green tea")

    assert "approve" in answer.text.lower()
    assert store.pending()[0]["text"] == "I prefer green tea"
    assert store.active() == []
    assert generator.payloads == []


def test_memory_list_and_forget_commands_are_deterministic(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.approve(store.propose("I prefer green tea"))
    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator, memory_store=store)

    listed = assistant.respond("What do you remember about me?")
    forgotten = assistant.respond("Forget green tea")

    assert "green tea" in listed.text.lower()
    assert "forgot" in forgotten.text.lower()
    assert store.active() == []
    assert generator.payloads == []


def test_do_not_remember_clears_window_and_proposals(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator, memory_store=store)
    assistant.respond("hello")
    assistant.respond("Remember that I prefer tea")

    assistant.respond("Do not remember this conversation")
    assistant.respond("another chat")

    assert store.pending() == []
    assert "recent_turns" not in generator.payloads[-1]
    assert "cannot" in assistant.respond("Remember that I like mango").text.lower()
    assert store.pending() == []


def test_global_memory_disable_makes_chat_stateless(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.approve(store.propose("I prefer tea"))
    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator, memory_store=store)
    assistant.respond("hello")
    store.set_enabled(False)

    assistant.respond("What tea do I prefer?")

    assert "recent_turns" not in generator.payloads[-1]
    assert "approved_memories" not in generator.payloads[-1]


def test_memory_store_failure_keeps_chat_available():
    class BrokenStore:
        def enabled(self):
            raise OSError("private path")

    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator, memory_store=BrokenStore())

    assert assistant.respond("hello").text == "A useful answer."
    assert "approved_memories" not in generator.payloads[-1]


def test_retrieval_failure_does_not_keep_new_turns_as_memory():
    class BrokenSearch:
        calls = 0

        def enabled(self):
            return True

        def search(self, _query, limit=3):
            self.calls += 1
            if self.calls <= 2:
                raise OSError("private path")
            return []

    generator = RecordingGenerator()
    assistant = AssistantService(FeedbackConfig(), generator=generator, memory_store=BrokenSearch())

    assistant.respond("first")
    assistant.respond("second")
    assistant.respond("third")

    assert "recent_turns" not in generator.payloads[-1]


def test_correct_command_does_not_silently_overwrite(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    store.approve(store.propose("I prefer green tea"))
    assistant = AssistantService(FeedbackConfig(), generator=RecordingGenerator(), memory_store=store)

    answer = assistant.respond("Correct that memory: I prefer black coffee")

    assert "dashboard" in answer.text.lower()
    assert store.active()[0]["text"] == "I prefer green tea"
