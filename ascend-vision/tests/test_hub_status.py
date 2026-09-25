from datetime import datetime, timezone

import pytest

from assistant.hub_status import parse_status_intent, render_status_answer
from assistant.tool_runtime import ToolCallError, ToolPolicyError, ToolRuntime, ToolSpec
from integrations.status_shelf import ShelfService, ShelfSnapshot


NOW = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def service(name, state, *, instance="desktop", service_type="agent"):
    return ShelfService(name, instance, service_type, state, NOW, NOW, 30)


def snapshot(*services):
    return ShelfSnapshot(NOW, tuple(services))


def test_tool_runtime_accepts_only_registered_read_only_zero_argument_calls():
    calls = []
    runtime = ToolRuntime()
    runtime.register(ToolSpec("hub_status", 1, "read-only", frozenset(), ShelfSnapshot),
                     lambda: calls.append("read") or snapshot())
    request = runtime.start_request()

    assert request.call("hub_status", {}) == snapshot()
    assert calls == ["read"]
    with pytest.raises(ToolPolicyError):
        request.call("hub_status", {})
    with pytest.raises(ToolPolicyError):
        runtime.start_request().call("hub_status", {"write": True})
    with pytest.raises(ToolPolicyError):
        runtime.start_request().call("unknown", {})
    with pytest.raises(ToolPolicyError):
        runtime.register(ToolSpec("danger", 1, "consequential", frozenset(), ShelfSnapshot), lambda: snapshot())


def test_tool_runtime_does_not_log_failed_tool_payload_or_credential(caplog):
    caplog.set_level("INFO")
    runtime = ToolRuntime()

    def failing_reader():
        raise RuntimeError("private shelf credential and payload")

    runtime.register(ToolSpec("hub_status", 1, "read-only", frozenset(), ShelfSnapshot), failing_reader)

    with pytest.raises(ToolCallError, match="could not complete"):
        runtime.start_request().call("hub_status", {})
    assert "private shelf credential and payload" not in caplog.text


@pytest.mark.parametrize("question", [
    "What is Ascend Hub doing?",
    "Is Codex CLI still working?",
    "What is Antigravity's status?",
    "Which AIs are online?",
    "Did Antigravity finish its work?",
    "Has Codex CLI completed its task?",
    "Is Core working?",
    "What is Vision's status?",
])
def test_status_intents_are_recognized(question):
    assert parse_status_intent(question) is not None


@pytest.mark.parametrize("question, target, asks_completion", [
    ("What is Claude's status?", "claude", False),
    ("what is claude's status?", "claude", False),
    ("Show Claude's status", "claude", False),
    ("Tell me Claude's status", "claude", False),
    ("Claude's status?", "claude", False),
    ("Can you show Claude's status?", "claude", False),
    ("Show Claude Code's status", "claude code", False),
    ("Tell me Claude Code's activity", "claude code", False),
    ("Can you show Claude Code's status?", "claude code", False),
    ("Claude Code's status?", "claude code", False),
    ("Tell me about Claude Code's status", "claude code", False),
    ("Can you check on Claude Code's status?", "claude code", False),
    ("What is Claude's activity?", "claude", False),
    ("What is Claude doing?", "claude", False),
    ("Is Claude running?", "claude", False),
    ("Is Claude active?", "claude", False),
    ("Is Claude still working?", "claude", False),
    ("is claude still active?", "claude", False),
    ("Did Claude finish its work?", "claude", True),
])
def test_explicit_unknown_agent_status_targets_the_named_agent(question, target, asks_completion):
    intent = parse_status_intent(question)

    assert intent is not None
    assert intent.targets == (target,)
    assert intent.asks_completion is asks_completion


@pytest.mark.parametrize("question", [
    "What is my focus status?",
    "What is My Focus's status?",
    "what is my focus's status?",
    "Is my focus active?",
    "Is My Focus still active?",
    "Show My Focus's status",
    "Are you working?",
    "Is it working?",
    "Are we done?",
    "Is everyone working?",
    "Is anyone active?",
    "What's today's status?",
    "Today's status?",
    "How is my vision?",
    "What is my vision status?",
    "Tell me about my memory",
    "Hello",
])
def test_unrelated_chat_is_not_status_intent(question):
    assert parse_status_intent(question) is None


def test_targeted_status_is_derived_from_shelf_state():
    intent = parse_status_intent("Is Codex CLI still working?")
    result = render_status_answer(intent, snapshot(
        service("antigravity", "idle"),
        service("codex-cli", "working"),
    ))

    assert result == "Codex CLI is working."
    assert "Antigravity" not in result


def test_multiple_named_agents_are_all_reported():
    intent = parse_status_intent("Are Antigravity and Codex CLI working?")
    result = render_status_answer(intent, snapshot(
        service("antigravity", "idle"),
        service("codex-cli", "working"),
    ))

    assert result == "Antigravity is idle; Codex CLI is working."


def test_missing_agent_in_multiple_targets_is_explicit():
    intent = parse_status_intent("Are Antigravity and Codex CLI working?")
    result = render_status_answer(intent, snapshot(service("antigravity", "idle")))

    assert "Antigravity is idle" in result
    assert "couldn't find Codex CLI" in result


def test_general_status_reports_multiple_instances_and_blocked_offline_states():
    intent = parse_status_intent("What is Ascend Hub doing?")
    result = render_status_answer(intent, snapshot(
        service("antigravity", "idle", instance="desktop"),
        service("antigravity", "working", instance="laptop"),
        service("codex-cli", "stuck"),
        service("other-agent", "offline"),
        service("model-provider", "working", service_type="provider"),
    ))

    assert "Antigravity (desktop) is idle" in result
    assert "Antigravity (laptop) is working" in result
    assert "Codex CLI is blocked" in result
    assert "Other Agent is offline" in result
    assert "Model Provider" not in result


def test_missing_named_agent_is_not_invented():
    intent = parse_status_intent("What is Antigravity's status?")
    result = render_status_answer(intent, snapshot(service("codex-cli", "working")))

    assert "couldn't find Antigravity" in result


def test_unknown_named_agent_is_explicitly_missing_from_shelf():
    intent = parse_status_intent("What is Claude's status?")

    assert render_status_answer(intent, snapshot(service("codex-cli", "working"))) == (
        "I couldn't find Claude in Ascend Hub's status shelf."
    )


def test_idle_does_not_claim_an_agent_finished():
    intent = parse_status_intent("Did Antigravity finish its work?")
    result = render_status_answer(intent, snapshot(service("antigravity", "idle")))

    assert "idle" in result
    assert "does not confirm whether its last task finished" in result


def test_completed_wording_uses_shelf_and_does_not_guess_completion():
    intent = parse_status_intent("Has Codex CLI completed its task?")
    result = render_status_answer(intent, snapshot(service("codex-cli", "idle")))

    assert "Codex CLI is idle" in result
    assert "does not confirm whether its last task finished" in result


def test_multiple_agent_completion_question_does_not_guess_task_outcomes():
    intent = parse_status_intent("Have Antigravity and Codex CLI finished?")
    result = render_status_answer(intent, snapshot(
        service("antigravity", "idle"), service("codex-cli", "working"),
    ))

    assert "Antigravity is idle; Codex CLI is working." in result
    assert "does not confirm whether their last tasks finished" in result


def test_empty_shelf_reports_no_registered_ai_agents():
    intent = parse_status_intent("Which AIs are online?")
    assert render_status_answer(intent, snapshot()) == "Ascend Hub has no AI agent status to report."
