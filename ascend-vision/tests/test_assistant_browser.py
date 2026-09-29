import time

from assistant.browser_intent import parse_browser_intent
from assistant.service import AssistantService
from config import BrowserAutomationConfig, FeedbackConfig
from browser.service import TaskReceipt


def test_browser_intent_requires_an_explicit_browser_action():
    assert parse_browser_intent('Vision, use the browser to find the official docs.') == 'find the official docs'
    assert parse_browser_intent('How do I automate a browser with Vision?') is None
    assert parse_browser_intent('Compare these two ideas for me.') is None


def test_remote_chat_cannot_start_a_local_browser_task():
    class Client:
        calls = 0

        def submit(self, _request):
            self.calls += 1
            return TaskReceipt('task-1', 'queued', 1)

    client = Client()
    service = AssistantService(
        FeedbackConfig(), browser_task_client=client,
        browser_automation_config=BrowserAutomationConfig(enabled=True),
    )

    answer = service.respond(
        'Use the browser to find the official documentation.',
        session_key=('local', 'phone_pwa', 'session-1'),
    )

    assert client.calls == 0
    assert 'only available' in answer.text.lower()


def test_local_browser_request_submits_quickly_with_owner_bound_transient_session():
    class Client:
        request = None

        def submit(self, request):
            self.request = request
            return TaskReceipt(request.task_id, 'queued', 1)

    client = Client()
    service = AssistantService(
        FeedbackConfig(), browser_task_client=client,
        browser_automation_config=BrowserAutomationConfig(enabled=True, provider='cerebras'),
    )
    before = time.monotonic()

    answer = service.respond(
        'Vision, use the browser to compare these two public documentation pages.',
        session_key=('local', 'dashboard', 'session-abc'),
    )

    assert time.monotonic() - before < 1
    assert client.request.goal == 'compare these two public documentation pages'
    assert client.request.provider == 'cerebras'
    assert client.request.session_key == ('local', 'dashboard', 'session-abc')
    assert 'task' in answer.text.lower()
    assert client.request.task_id in answer.text


def test_only_voice_browser_tasks_are_registered_for_completion_announcement():
    class Client:
        def submit(self, request):
            return TaskReceipt(request.task_id, 'queued', 1)

    watched = []
    service = AssistantService(
        FeedbackConfig(), browser_task_client=Client(),
        browser_automation_config=BrowserAutomationConfig(enabled=True),
        browser_task_submitted=lambda task_id, key: watched.append((task_id, key)),
    )
    voice = ('local', 'voice', 'voice-session')
    dashboard = ('local', 'dashboard', 'dashboard-session')

    service.respond('Use the browser to find the official docs.', session_key=voice)
    service.respond('Use the browser to find the official docs.', session_key=dashboard)

    assert len(watched) == 1
    assert watched[0][1] == voice


def test_browser_feature_disabled_does_not_submit_tasks():
    class Client:
        def submit(self, _request):
            raise AssertionError('disabled browser feature must not submit')

    service = AssistantService(
        FeedbackConfig(), browser_task_client=Client(),
        browser_automation_config=BrowserAutomationConfig(),
    )

    answer = service.respond(
        'Use the browser to find the official docs.',
        session_key=('local', 'voice', 'voice-session'),
    )

    assert 'not enabled' in answer.text.lower()
