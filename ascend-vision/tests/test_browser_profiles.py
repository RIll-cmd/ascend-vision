import base64

import pytest

from browser.profile_store import BrowserProfileStore


def test_saved_profile_is_encrypted_at_rest_and_scoped_to_the_owner(tmp_path):
    store = BrowserProfileStore(
        tmp_path,
        protect=base64.b64encode,
        unprotect=base64.b64decode,
    )
    state = {'cookies': [{'name': 'session', 'value': 'do-not-write-plaintext'}], 'origins': []}

    store.save('owner-a', 'work-account', state)

    files = list(tmp_path.rglob('*.dpapi'))
    assert len(files) == 1
    assert b'do-not-write-plaintext' not in files[0].read_bytes()
    assert store.load('owner-a', 'work-account') == state
    assert store.list_profiles('owner-a') == ['work-account']
    assert store.list_profiles('owner-b') == []


def test_clearing_a_profile_removes_only_that_owner_profile(tmp_path):
    store = BrowserProfileStore(tmp_path, protect=bytes, unprotect=bytes)
    store.save('owner-a', 'first', {'cookies': [], 'origins': []})
    store.save('owner-a', 'second', {'cookies': [], 'origins': []})
    store.save('owner-b', 'first', {'cookies': [], 'origins': []})

    assert store.clear('owner-a', 'first') is True
    assert store.clear('owner-a', 'first') is False
    assert store.list_profiles('owner-a') == ['second']
    assert store.list_profiles('owner-b') == ['first']


@pytest.mark.parametrize('profile_id', ['..', '../escape', 'a/b', 'a\\b', 'bad name', ''])
def test_profile_ids_cannot_escape_the_managed_profile_root(tmp_path, profile_id):
    store = BrowserProfileStore(tmp_path, protect=bytes, unprotect=bytes)

    with pytest.raises(ValueError):
        store.save('owner-a', profile_id, {'cookies': [], 'origins': []})


def test_profile_state_is_bounded_and_must_be_an_object(tmp_path):
    store = BrowserProfileStore(tmp_path, protect=bytes, unprotect=bytes, max_state_bytes=128)

    with pytest.raises(ValueError, match='size'):
        store.save('owner-a', 'work', {'payload': 'x' * 256})
    with pytest.raises(ValueError, match='object'):
        store.save('owner-a', 'work', ['not', 'an', 'object'])


def test_dpapi_mode_refuses_to_fall_back_to_unencrypted_profile_data(monkeypatch, tmp_path):
    monkeypatch.setattr('browser.profile_store.os.name', 'posix')
    store = BrowserProfileStore(tmp_path)

    with pytest.raises(RuntimeError, match='Windows DPAPI'):
        store.save('owner-a', 'work', {'cookies': [], 'origins': []})
