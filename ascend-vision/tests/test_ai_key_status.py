import pytest

from ai_key_status import KeyStatusStore, fingerprint, retry_seconds
from llm_router import ProviderKeyPool


class Limited(Exception):
    status_code = 429


@pytest.mark.parametrize('message, expected', [
    ("429 RESOURCE_EXHAUSTED ... Please retry in 9h42m20.769s. ... 'retryDelay': '34940s'", 34940),
    ('Rate limit reached. Please try again in 7.5s.', 7.5),
    ('Please try again in 2m30s', 150),
    ('429 Too Many Requests', 60),
])
def test_retry_hint_is_read_from_the_provider_message(message, expected):
    assert retry_seconds(Exception(message)) == pytest.approx(expected)


def test_store_keeps_only_fingerprints_and_reports_limited_keys(tmp_path):
    clock = [1_000.0]
    store = KeyStatusStore(tmp_path / 'status.sqlite3', clock=lambda: clock[0])
    store.record_limit('gemini', 'secret-key-1', 3600)
    raw = b''.join(path.read_bytes() for path in tmp_path.glob('status.sqlite3*'))  # includes the WAL side file
    assert b'secret-key-1' not in raw and fingerprint('secret-key-1').encode() in raw
    summary = store.summary({'gemini': ['secret-key-1', 'secret-key-2'], 'groq': []})
    assert summary == {'gemini': {'total': 2, 'limited': 1, 'freeAt': summary['gemini']['freeAt']}}
    assert summary['gemini']['freeAt'] is not None
    clock[0] += 3601
    assert store.summary({'gemini': ['secret-key-1', 'secret-key-2']})['gemini']['limited'] == 0


def test_pool_skips_keys_another_process_found_limited_and_records_new_limits(tmp_path):
    store = KeyStatusStore(tmp_path / 'status.sqlite3')
    keys = ['k1', 'k2', 'k3', 'k4', 'k5']
    store.record_limit('groq', 'k1', 600)
    store.record_limit('groq', 'k2', 600)
    used = []

    def call(client):
        used.append(client)
        if client == 'k3':
            raise Limited('Rate limit reached. Please try again in 30s.')
        return f'answer from {client}'

    pool = ProviderKeyPool('groq', keys, lambda key: key, status_store=store)
    assert pool.execute_with_failover(call, max_attempts=2) == 'answer from k4'
    assert used == ['k3', 'k4']  # limited keys cost no attempt
    assert store.summary({'groq': keys})['groq']['limited'] == 3


def test_when_every_key_is_limited_one_real_attempt_still_happens(tmp_path):
    store = KeyStatusStore(tmp_path / 'status.sqlite3')
    for key in ('a', 'b'):
        store.record_limit('gemini', key, 600)
    used = []

    def call(client):
        used.append(client)
        raise Limited('429 quota')

    pool = ProviderKeyPool('gemini', ['a', 'b'], lambda key: key, status_store=store)
    with pytest.raises(Limited):
        pool.execute_with_failover(call, max_attempts=2)
    assert used == ['a']


def test_a_broken_status_store_never_breaks_the_ai_call():
    class Broken:
        def limited_until(self, *_args):
            raise OSError('disk gone')

        def record_limit(self, *_args):
            raise OSError('disk gone')

    calls = []

    def call(client):
        calls.append(client)
        if client == 'a':
            raise Limited('429')
        return 'ok'

    pool = ProviderKeyPool('groq', ['a', 'b'], lambda key: key, status_store=Broken())
    assert pool.execute_with_failover(call) == 'ok'
    assert calls == ['a', 'b']


def test_spoken_warning_happens_once_per_outage_and_again_after_recovery():
    from ai_key_status import KeyLowNotifier
    notifier = KeyLowNotifier()
    out = {'gemini': {'total': 2, 'limited': 2, 'freeAt': '2026-10-13T01:40:00+00:00'},
           'groq': {'total': 5, 'limited': 4, 'freeAt': None}}
    first = notifier.messages(out)
    assert len(first) == 1 and first[0].startswith('Heads up: all your Gemini keys are used up until about')
    assert notifier.messages(out) == []                      # not repeated
    out['gemini']['limited'] = 1
    assert notifier.messages(out) == []                      # recovered: re-armed, nothing said
    out['gemini']['limited'] = 2
    assert len(notifier.messages(out)) == 1                  # said again for the next outage


def test_keys_are_read_from_the_configured_env_names(monkeypatch):
    from ai_key_status import keys_from_env
    monkeypatch.setenv('MY_GEMINI', 'a, b;c')
    monkeypatch.delenv('MY_GROQ', raising=False)
    assert keys_from_env({'gemini': 'MY_GEMINI', 'groq': 'MY_GROQ'}) == {'gemini': ['a', 'b', 'c'], 'groq': []}


def test_vision_window_state_reports_key_counts_without_key_text():
    from focus_ui import FocusUI
    calls = []

    def summary():
        calls.append(1)
        return {'gemini': {'total': 2, 'limited': 2, 'freeAt': '2026-10-13T01:40:00+00:00'}}

    ui = FocusUI()
    client = ui.app.test_client()
    assert 'aiKeys' not in client.get('/api/fairy/state').json
    ui.bind_key_status(summary)
    first = client.get('/api/fairy/state').json['aiKeys']
    client.get('/api/fairy/state')
    assert first['gemini'] == {'total': 2, 'limited': 2, 'freeAt': '2026-10-13T01:40:00+00:00'}
    assert len(calls) == 1  # cached between polls
