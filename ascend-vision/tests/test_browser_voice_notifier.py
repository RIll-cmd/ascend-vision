import threading
import time
from types import SimpleNamespace

from integrations.browser_voice_notifier import BrowserVoiceCompletionNotifier


class FakeBrowserClient:
    def __init__(self, page):
        self.page = page
        self.calls = []

    def events(self, task_id, after, session_key):
        self.calls.append((task_id, after, session_key))
        return self.page


class FakeSpeech:
    def __init__(self):
        self.announcement = None
        self.guard = None
        self.ready = threading.Event()

    def speak_guarded_announcement(self, text, guard):
        self.announcement = text
        self.guard = guard
        self.ready.set()
        return True


def test_voice_notifier_announces_terminal_result_only_for_its_session():
    key = ('local', 'voice', 'voice-session-1')
    page = SimpleNamespace(state='completed', next_cursor=3, result={
        'status': 'completed',
        'findings': [{'text': 'Found the documented timeout setting.', 'sources': ['https://docs.example/']}],
    })
    client, speech = FakeBrowserClient(page), FakeSpeech()
    notifier = BrowserVoiceCompletionNotifier(
        client, speech, lambda session_key: session_key == key, poll_interval_seconds=.01,
    )
    notifier.start()
    notifier.watch('task-1', key)
    try:
        assert speech.ready.wait(1)
        assert speech.announcement == 'Browser research complete: Found the documented timeout setting.'
        assert speech.guard()
        assert client.calls == [('task-1', 0, key)]
    finally:
        notifier.stop()


def test_voice_notifier_does_not_watch_non_voice_or_speak_stale_session():
    key = ('local', 'voice', 'voice-session-1')
    page = SimpleNamespace(state='completed', next_cursor=2, result={
        'status': 'completed', 'findings': [{'text': 'Done.', 'sources': []}],
    })
    client, speech = FakeBrowserClient(page), FakeSpeech()
    notifier = BrowserVoiceCompletionNotifier(client, speech, lambda _key: False, poll_interval_seconds=.01)
    notifier.start()
    try:
        notifier.watch('task-dashboard', ('local', 'dashboard', 'local-dashboard'))
        time.sleep(.03)
        assert not client.calls
        notifier.watch('task-voice', key)
        time.sleep(.03)
        assert not speech.ready.is_set()
        assert speech.announcement is None
    finally:
        notifier.stop()
