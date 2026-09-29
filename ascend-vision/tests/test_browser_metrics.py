import json
from datetime import datetime, timezone

from browser.metrics import MetricsStore, ProviderCallUsage, RunOutcome, RunStart


def test_metrics_keep_unknown_usage_unknown_and_export_no_task_content(tmp_path):
    store = MetricsStore(tmp_path / 'metrics.sqlite3', enabled=True)
    run_id = store.begin(RunStart('local_dashboard', 'live_public'))
    assert run_id
    assert store.record_call(run_id, ProviderCallUsage(
        attempt_id='attempt-id-00000001', provider='gemini', model_requested='test-model',
        model_actual='test-model', input_tokens=None, output_tokens=None,
        outcome='success', reserved_cost_usd_micros=123,
    ))
    assert store.finish(run_id, RunOutcome('completed', 'finished'))

    summary = store.summary('live_public')
    call = store.export()['runs'][0]
    assert summary['accounting']['unknown_token_calls'] == 1
    assert summary['accounting']['unknown_cost_calls'] == 1
    assert summary['accounting']['estimated_cost_usd_micros'] is None
    assert call['reserved_cost_usd_micros'] == 123
    assert call['estimated_cost_usd_micros'] is None
    assert 'PRIVATE_GOAL_SENTINEL' not in json.dumps(store.export())


def test_metrics_mark_unfinished_runs_interrupted_after_restart(tmp_path):
    path = tmp_path / 'metrics.sqlite3'
    first = MetricsStore(path, enabled=True)
    first.begin(RunStart('local_dashboard', 'live_public'))

    restarted = MetricsStore(path, enabled=True)

    summary = restarted.summary('all')
    assert summary['eligible'] == 1
    assert summary['interrupted'] == 1


def test_metrics_retention_and_run_cap_are_enforced_on_write(tmp_path):
    now = [1_800_000_000.0]
    store = MetricsStore(tmp_path / 'metrics.sqlite3', enabled=True, retention_days=1,
                         max_runs=2, clock=lambda: now[0])
    store.begin(RunStart('local_dashboard', 'live_public', started_at=now[0] - 2 * 86400))
    store.begin(RunStart('local_dashboard', 'live_public', started_at=now[0]))
    store.begin(RunStart('local_dashboard', 'live_public', started_at=now[0] + 1))

    result = store.export()
    assert result['total'] == 2
    assert all(row['started_at'] >= now[0] for row in result['runs'])


def test_metrics_collection_is_opt_in_and_clear_turns_it_off(tmp_path):
    store = MetricsStore(tmp_path / 'metrics.sqlite3', enabled=False)
    assert store.begin(RunStart('local_dashboard', 'live_public')) is None

    store.set_enabled(True)
    assert store.begin(RunStart('local_dashboard', 'live_public'))
    store.clear()

    assert not store.enabled
    assert store.summary('all')['eligible'] == 0


def test_feedback_accepts_only_a_terminal_run_and_connections_close(tmp_path):
    store = MetricsStore(tmp_path / 'metrics.sqlite3', enabled=True)
    run_id = store.begin(RunStart('local_dashboard', 'live_public'))
    assert not store.feedback(run_id, 'worked')
    store.finish(run_id, RunOutcome('completed', 'finished'))
    assert store.feedback(run_id, 'worked')

    store.clear()

    assert store.summary('all')['eligible'] == 0


def test_task_goal_is_not_written_to_the_metrics_database(tmp_path):
    from browser.contracts import BrowserTaskRequest
    from browser.service import BrowserTaskService, _Task

    sentinel = 'PRIVATE_GOAL_SENTINEL_95e27'
    request = BrowserTaskRequest.from_payload({
        'schema_version': 1, 'task_id': 'privacy-run',
        'session_key': ['owner', 'dashboard', 'session'], 'goal': sentinel,
        'provider': 'gemini', 'scope_mode': 'public_research', 'expires_at': 1_900_000_000,
    })
    path = tmp_path / 'metrics.sqlite3'
    store = MetricsStore(path, enabled=True)
    service = BrowserTaskService(metrics_store=store)

    run_id = service._begin_metrics(_Task(request))

    assert run_id
    assert sentinel.encode() not in path.read_bytes()


def test_owner_feedback_is_terminal_and_session_scoped(tmp_path):
    import pytest
    from browser.contracts import BrowserTaskRequest
    from browser.service import BrowserTaskService, TaskNotFound, _Task

    owner_key = ('owner', 'dashboard', 'session')
    task_request = BrowserTaskRequest.from_payload({
        'schema_version': 1, 'task_id': 'feedback-run', 'session_key': list(owner_key),
        'goal': 'private goal', 'provider': 'gemini', 'scope_mode': 'public_research',
        'expires_at': 1_900_000_000,
    })
    metrics = MetricsStore(tmp_path / 'metrics.sqlite3', enabled=True)
    service = BrowserTaskService(metrics_store=metrics)
    task = _Task(task_request, metrics_run_id=metrics.begin(RunStart('local_dashboard', 'live_public')))
    service._tasks[task_request.task_id] = task

    with pytest.raises(ValueError, match='terminal state'):
        service.feedback(task_request.task_id, 'worked', owner_key)
    service._finish(task, 'completed', 'finished', {'status': 'completed'})
    with pytest.raises(TaskNotFound):
        service.feedback(task_request.task_id, 'worked', ('other-owner', 'dashboard', 'session'))
    assert service.feedback(task_request.task_id, 'worked', owner_key)
