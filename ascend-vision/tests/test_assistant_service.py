from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import threading
import time

import pytest

from assistant.service import AssistantService
from assistant.tool_runtime import ToolRuntime, ToolSpec
from config import FeedbackConfig, LLMConfig
from integrations.status_shelf import ShelfService, ShelfSnapshot


class Generator:
    def __init__(self, reply="The task is moving."):
        self.reply = reply
        self.calls = []

    def generate_chat(self, text, context, max_words=25):
        self.calls.append((text, context, max_words))
        return self.reply


def test_assistant_returns_the_provider_answer():
    generator = Generator()
    service = AssistantService(FeedbackConfig(), LLMConfig(), generator=generator)

    reply = service.respond("  Status?  ", max_words=18)

    assert reply.text == "The task is moving."
    assert reply.source == "model"
    assert generator.calls == [("Status?", None, 18)]


def test_assistant_uses_safe_offline_answer_when_provider_fails():
    class FailingGenerator:
        def generate_chat(self, text, context, max_words=25):
            raise RuntimeError("private provider secret")

    service = AssistantService(FeedbackConfig(), LLMConfig(), generator=FailingGenerator())

    reply = service.respond("How am I doing?")

    assert reply.text
    assert reply.source == "offline"
    assert "private provider secret" not in reply.text


def test_assistant_uses_offline_answer_for_blank_provider_text():
    service = AssistantService(FeedbackConfig(), LLMConfig(), generator=Generator("  "))

    reply = service.respond("Hello")

    assert reply.text
    assert reply.source == "offline"


@pytest.mark.parametrize("text", ["", " \t ", None])
def test_assistant_rejects_empty_input(text):
    service = AssistantService(FeedbackConfig(), LLMConfig(), generator=Generator())

    with pytest.raises(ValueError, match="nonempty"):
        service.respond(text)


def test_assistant_serializes_generator_calls_from_two_channels():
    class ConcurrentGenerator:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def generate_chat(self, text, context, max_words=25):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.03)
            with self.lock:
                self.active -= 1
            return text

    generator = ConcurrentGenerator()
    service = AssistantService(FeedbackConfig(), LLMConfig(), generator=generator)

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(service.respond, ["voice", "dashboard"]))

    assert {reply.text for reply in replies} == {"voice", "dashboard"}
    assert generator.max_active == 1


def status_runtime(reader=None):
    moment = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)
    snapshot = ShelfSnapshot(moment, (
        ShelfService("codex-cli", "desktop", "agent", "working", moment, moment, 30),
    ))
    runtime = ToolRuntime()
    runtime.register(ToolSpec("hub_status", 1, "read-only", frozenset(), ShelfSnapshot),
                     reader or (lambda: snapshot))
    return runtime


def test_assistant_answers_hub_status_without_model_or_stale_session_history():
    generator = Generator("I guess Codex is idle")
    service = AssistantService(FeedbackConfig(), generator=generator,
                               tool_runtime=status_runtime())

    reply = service.respond("Is Codex CLI still working?")
    service.respond("Hello")

    assert reply.text == "Codex CLI is working."
    assert reply.source == "tool"
    assert len(generator.calls) == 1
    assert generator.calls[0][1] is None


def test_assistant_fails_closed_when_hub_status_tool_is_unavailable():
    generator = Generator("I guess it is finished")
    service = AssistantService(FeedbackConfig(), generator=generator)

    reply = service.respond("Did Antigravity finish its work?")

    assert "cannot verify" in reply.text.lower()
    assert "finished" not in reply.text.lower()
    assert generator.calls == []


def test_assistant_fails_closed_when_hub_status_reader_raises():
    def failing_reader():
        raise RuntimeError("private credential")

    generator = Generator("I guess it is idle")
    service = AssistantService(FeedbackConfig(), generator=generator,
                               tool_runtime=status_runtime(failing_reader))

    reply = service.respond("What is Ascend Hub doing?")

    assert "cannot verify" in reply.text.lower()
    assert "private credential" not in reply.text
    assert generator.calls == []


def test_unrelated_chat_does_not_fetch_hub_status():
    def failing_reader():
        raise AssertionError("status tool should not run")

    generator = Generator("Your focus is going well.")
    service = AssistantService(FeedbackConfig(), generator=generator,
                               tool_runtime=status_runtime(failing_reader))

    reply = service.respond("What is my focus status?")

    assert reply.source == "model"
    assert len(generator.calls) == 1
