from concurrent.futures import ThreadPoolExecutor
import threading
import time

import pytest

from assistant.service import AssistantService
from config import FeedbackConfig, LLMConfig


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
