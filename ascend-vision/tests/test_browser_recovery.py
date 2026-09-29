import pytest
import subprocess
import sys
import textwrap

from browser.journal import BrowserActionJournal, DuplicateAction, JournalCapacityExceeded


def test_journal_records_intent_then_result_without_storing_action_content(tmp_path):
    journal = BrowserActionJournal(tmp_path / 'actions.sqlite3')
    journal.begin('task-1', 'owner-1', 'action-1', 'fill', 'https://example.org', 'proposal-hash')
    assert journal.get('action-1')['state'] == 'dispatching'
    journal.finish('action-1', 'observed')
    row = journal.get('action-1')
    assert row['state'] == 'observed'
    assert 'summary' not in row
    assert 'secret' not in repr(row)


def test_journal_rejects_a_duplicate_action_id(tmp_path):
    journal = BrowserActionJournal(tmp_path / 'actions.sqlite3')
    journal.begin('task-1', 'owner-1', 'action-1', 'submit', 'https://example.org', 'hash')
    with pytest.raises(DuplicateAction):
        journal.begin('task-1', 'owner-1', 'action-1', 'submit', 'https://example.org', 'hash')


def test_unacknowledged_dispatch_is_marked_unknown_on_restart(tmp_path):
    path = tmp_path / 'actions.sqlite3'
    journal = BrowserActionJournal(path)
    journal.begin('task-1', 'owner-1', 'action-1', 'submit', 'https://example.org', 'hash')
    restarted = BrowserActionJournal(path)
    assert restarted.get('action-1')['state'] == 'unknown'


@pytest.mark.parametrize('crash_point,expected_effect,expected_observation', [
    ('before_dispatch', False, False),
    ('after_dispatch', True, False),
    ('before_acknowledgment', True, True),
])
def test_process_crash_at_dispatch_boundaries_recovers_as_unknown(
        tmp_path, crash_point, expected_effect, expected_observation):
    journal_path = tmp_path / 'actions.sqlite3'
    effect_path = tmp_path / 'external-effect.marker'
    observation_path = tmp_path / 'observation.marker'
    crash_script = textwrap.dedent('''
        import os
        import sys
        from pathlib import Path
        from browser.journal import BrowserActionJournal

        journal_path, effect_path, observation_path, crash_point = sys.argv[1:]
        journal = BrowserActionJournal(journal_path)
        journal.begin('task-1', 'owner-1', 'action-1', 'fill', 'https://example.org', 'digest')
        if crash_point != 'before_dispatch':
            Path(effect_path).write_text('site may have changed', encoding='utf-8')
        if crash_point == 'before_acknowledgment':
            Path(observation_path).write_text('fresh page observed', encoding='utf-8')
        os._exit(23)
    ''')
    crashed = subprocess.run(
        [sys.executable, '-c', crash_script, str(journal_path), str(effect_path),
         str(observation_path), crash_point],
        capture_output=True, text=True, check=False,
    )

    assert crashed.returncode == 23
    assert effect_path.exists() is expected_effect
    assert observation_path.exists() is expected_observation
    restarted = BrowserActionJournal(journal_path)
    assert restarted.get('action-1')['state'] == 'unknown'


def test_journal_retention_is_bounded_and_expired_rows_are_pruned(tmp_path):
    now = [1000.0]
    path = tmp_path / 'actions.sqlite3'
    journal = BrowserActionJournal(path, clock=lambda: now[0], max_rows=1, retention_seconds=10)
    journal.begin('task-1', 'owner-1', 'action-1', 'fill', 'https://example.org', 'hash')
    journal.finish('action-1', 'observed')
    with pytest.raises(JournalCapacityExceeded):
        journal.begin('task-2', 'owner-1', 'action-2', 'fill', 'https://example.org', 'hash')
    now[0] += 11
    compacted = BrowserActionJournal(path, clock=lambda: now[0], max_rows=1, retention_seconds=10)
    compacted.begin('task-2', 'owner-1', 'action-2', 'fill', 'https://example.org', 'hash')
    assert compacted.get('action-1') is None


def test_remote_task_journal_uses_same_capacity_limit(tmp_path):
    from browser.remote_contracts import RemoteBrowserBinding

    journal = BrowserActionJournal(tmp_path / 'actions.sqlite3', max_rows=1)
    binding = RemoteBrowserBinding.from_payload({
        'task_id': '51931fa8-1e2a-4d24-a098-3406ed4fd0a7', 'owner_id': 'owner-1',
        'channel': 'phone_pwa', 'browser_session_id': '65d41ed4-7a3c-4371-a019-38c1d4bd35c5',
        'laptop_id': 'laptop-1', 'broker_boot_id': 'boot-1', 'lease_id': 'lease-1',
        'fence': 1, 'scope_id': 'public_research', 'scope_version': 1,
    })
    journal.record_remote_task(binding)

    next_binding = RemoteBrowserBinding.from_payload({
        **binding.to_payload(), 'task_id': '962ed617-69ea-45fe-86c6-981fc2648e61',
    })
    with pytest.raises(JournalCapacityExceeded):
        journal.record_remote_task(next_binding)


def test_remote_task_journal_fences_started_tasks_after_broker_restart(tmp_path):
    from browser.remote_contracts import RemoteBrowserBinding

    path = tmp_path / 'actions.sqlite3'
    binding = RemoteBrowserBinding.from_payload({
        'task_id': '51931fa8-1e2a-4d24-a098-3406ed4fd0a7', 'owner_id': 'owner-1',
        'channel': 'phone_pwa', 'browser_session_id': '65d41ed4-7a3c-4371-a019-38c1d4bd35c5',
        'laptop_id': 'laptop-1', 'broker_boot_id': 'boot-1', 'lease_id': 'lease-1',
        'fence': 1, 'scope_id': 'public_research', 'scope_version': 1,
    })
    journal = BrowserActionJournal(path)
    journal.record_remote_task(binding, state='started')
    restarted = BrowserActionJournal(path)

    assert restarted.remote_reconciliations()[0]['state'] == 'unknown'


def test_remote_reconciliation_contains_only_content_free_action_evidence(tmp_path):
    from browser.remote_contracts import RemoteBrowserBinding

    path = tmp_path / 'actions.sqlite3'
    binding = RemoteBrowserBinding.from_payload({
        'task_id': '51931fa8-1e2a-4d24-a098-3406ed4fd0a7', 'owner_id': 'owner-1',
        'channel': 'phone_pwa', 'browser_session_id': '65d41ed4-7a3c-4371-a019-38c1d4bd35c5',
        'laptop_id': 'laptop-1', 'broker_boot_id': 'boot-1', 'lease_id': 'lease-1',
        'fence': 1, 'scope_id': 'public_research', 'scope_version': 1,
    })
    journal = BrowserActionJournal(path)
    journal.record_remote_task(binding, state='started')
    journal.begin(binding.task_id, binding.owner_id, 'action-1', 'fill',
                  'https://example.org', 'a' * 64)
    restarted = BrowserActionJournal(path)

    evidence = restarted.remote_reconciliations()[0]
    assert evidence['attempts'] == [{
        'action_id': 'action-1', 'action': 'fill', 'proposal_digest': 'a' * 64,
        'outcome': 'unknown',
    }]
    assert 'https://example.org' not in repr(evidence)
    assert 'lease-1' not in repr(evidence)
