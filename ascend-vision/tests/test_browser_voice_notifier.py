import threading
import time
from types import SimpleNamespace

import pytest

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
        self.ready = threading.Event()

    def speak_announcement(self, text):
        self.announcement = text
        self.ready.set()
        return True


def _run(page, key, current=True, publisher=None):
    client, speech = FakeBrowserClient(page), FakeSpeech()
    notifier = BrowserVoiceCompletionNotifier(
        client, speech, lambda session_key: current and session_key == key,
        poll_interval_seconds=.01, chat_publisher=publisher,
    )
    notifier.start()
    notifier.watch('task-1', key)
    return notifier, client, speech


def test_finished_lookup_is_shown_in_chat_and_spoken_in_a_short_form():
    key = ('local', 'voice', 'voice-session-1')
    long_finding = 'Vite is a build tool. ' + ('It has many details about configuration. ' * 30)
    page = SimpleNamespace(state='completed', next_cursor=3, result={
        'status': 'completed',
        'findings': [{'text': long_finding, 'sources': ['https://vite.dev/guide/', 'javascript:alert(1)']}],
    })
    chats = []
    notifier, client, speech = _run(page, key, publisher=chats.append)
    try:
        assert speech.ready.wait(1)
        assert speech.announcement.startswith("Here's what I found. Vite is a build tool.")
        assert len(speech.announcement) < 260
        assert 'http' not in speech.announcement
        assert len(chats) == 1
        assert chats[0].startswith("Here's what I found: Vite is a build tool.")
        assert len(chats[0]) < 900
        assert 'Source: https://vite.dev/guide/' in chats[0]
        assert 'javascript:' not in chats[0]
        assert client.calls == [('task-1', 0, key)]
    finally:
        notifier.stop()


@pytest.mark.parametrize('result, chat_has, spoken_has', [
    ({'status': 'failed', 'reason': 'A page took too long to load.', 'findings': []},
     "couldn't finish that lookup. A page took too long", "Sorry, I couldn't finish"),
    ({'status': 'partial', 'reason': 'The browser task reached its time limit.', 'findings': []},
     "couldn't finish that lookup. The browser task reached its time limit.", "Sorry"),
    ({'status': 'cancelled', 'findings': []}, 'I stopped', 'I stopped'),
    ({'status': 'partial', 'findings': [{'text': 'Half an answer.', 'sources': []}]},
     'only partly finished', 'only partly finished'),
])
def test_every_ending_is_explained_in_plain_words(result, chat_has, spoken_has):
    key = ('local', 'voice', 'voice-session-1')
    chats = []
    notifier, _client, speech = _run(SimpleNamespace(state=result['status'], next_cursor=1, result=result), key,
                                     publisher=chats.append)
    try:
        assert speech.ready.wait(1)
        assert chat_has in chats[0]
        assert spoken_has in speech.announcement
    finally:
        notifier.stop()


def test_a_chat_failure_never_blocks_the_spoken_answer():
    key = ('local', 'voice', 'voice-session-1')
    page = SimpleNamespace(state='completed', next_cursor=2, result={
        'status': 'completed', 'findings': [{'text': 'Done and dusted.', 'sources': []}]})

    def broken(_text):
        raise RuntimeError('chat is closed')

    notifier, _client, speech = _run(page, key, publisher=broken)
    try:
        assert speech.ready.wait(1)
        assert 'Done and dusted.' in speech.announcement
    finally:
        notifier.stop()


def test_voice_notifier_does_not_watch_non_voice_or_speak_stale_session():
    key = ('local', 'voice', 'voice-session-1')
    page = SimpleNamespace(state='completed', next_cursor=2, result={
        'status': 'completed', 'findings': [{'text': 'Done.', 'sources': []}],
    })
    chats = []
    client, speech = FakeBrowserClient(page), FakeSpeech()
    notifier = BrowserVoiceCompletionNotifier(client, speech, lambda _key: False, poll_interval_seconds=.01,
                                              chat_publisher=chats.append)
    notifier.start()
    try:
        notifier.watch('task-dashboard', ('local', 'dashboard', 'local-dashboard'))
        time.sleep(.03)
        assert not client.calls
        notifier.watch('task-voice', key)
        time.sleep(.03)
        assert not speech.ready.is_set()
        assert speech.announcement is None and chats == []
    finally:
        notifier.stop()
