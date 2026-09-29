from datetime import date, timedelta

import pytest

from browser.budgets import BudgetTracker, ModelPrice, PriceTable, PricingUnavailable


def price_table(*, effective_date=None):
    return PriceTable('fixture-v1', [ModelPrice(
        'gemini', 'test-model', 1_000_000, 1_000_000, 'fixture-v1',
        'https://ai.google.dev/gemini-api/docs/pricing',
    )], (effective_date or date.today()).isoformat(), 'USD')


def test_reservation_requires_covered_model_and_supported_token_bound():
    tracker = BudgetTracker(price_table())
    with pytest.raises(PricingUnavailable):
        tracker.reserve('run-id', 'groq', 'unknown-model', conservative_input_tokens=20,
                        max_output_tokens=10)
    with pytest.raises(PricingUnavailable):
        tracker.reserve('run-id', 'gemini', 'test-model', conservative_input_tokens=None,
                        max_output_tokens=10)


def test_persistent_ledger_enforces_spend_across_tracker_restart(tmp_path):
    ledger = tmp_path / 'pilot.sqlite3'
    first = BudgetTracker(price_table(), daily_cost_usd_micros=100,
                          pilot_cost_usd_micros=4, ledger_path=ledger)
    first.reserve('run-1', 'gemini', 'test-model', conservative_input_tokens=1,
                  max_output_tokens=1, attempt_id='attempt-id-00000001')
    first.reserve('run-2', 'gemini', 'test-model', conservative_input_tokens=1,
                  max_output_tokens=1, attempt_id='attempt-id-00000002')

    restarted = BudgetTracker(price_table(), daily_cost_usd_micros=100,
                              pilot_cost_usd_micros=4, ledger_path=ledger)
    with pytest.raises(PricingUnavailable, match='configured cost target'):
        restarted.reserve('run-3', 'gemini', 'test-model', conservative_input_tokens=1,
                          max_output_tokens=1, attempt_id='attempt-id-00000003')


def test_price_table_rejects_stale_or_future_pricing():
    tracker = BudgetTracker(price_table(effective_date=date.today() - timedelta(days=31)))
    with pytest.raises(PricingUnavailable, match='older than 30 days'):
        tracker.price_table.require_fresh()


def test_price_table_rejects_non_official_source():
    with pytest.raises(ValueError, match='official provider pricing page'):
        ModelPrice('gemini', 'test-model', 1, 1, 'fixture-v1', 'https://example.com/pricing')


def test_service_keeps_actual_cost_unknown_when_provider_usage_is_missing(tmp_path):
    from browser.contracts import BrowserTaskRequest
    from browser.metrics import MetricsStore, RunStart, ProviderCallUsage
    from browser.service import BrowserTaskService, _Task

    request = BrowserTaskRequest.from_payload({
        'schema_version': 1, 'task_id': 'cost-run',
        'session_key': ['owner', 'dashboard', 'session'], 'goal': 'private task',
        'provider': 'gemini', 'scope_mode': 'public_research', 'expires_at': 1_900_000_000,
    })
    metrics = MetricsStore(tmp_path / 'metrics.sqlite3', enabled=True)
    run_id = metrics.begin(RunStart('local_dashboard', 'live_public'))
    tracker = BudgetTracker(price_table(), ledger_path=tmp_path / 'budget.sqlite3')
    service = BrowserTaskService(metrics_store=metrics, budget_tracker=tracker)
    task = _Task(request, metrics_run_id=run_id)
    reservation = tracker.reserve(run_id, 'gemini', 'test-model', conservative_input_tokens=100,
                                 max_output_tokens=20, attempt_id='attempt-id-00000009')
    task.provider_reservations[reservation.reservation_id] = reservation

    service._record_provider_call(task, ProviderCallUsage(
        attempt_id=reservation.reservation_id, provider='gemini', model_requested='test-model',
        model_actual='test-model', input_tokens=None, output_tokens=None, outcome='provider_error',
    ))

    call = metrics.export()['runs'][0]
    assert call['estimated_cost_usd_micros'] is None
    assert call['reserved_cost_usd_micros'] == reservation.cost_usd_micros


def test_metrics_store_failure_blocks_paid_call_before_provider_boundary(tmp_path):
    from browser.budgets import PricingUnavailable
    from browser.contracts import BrowserTaskRequest
    from browser.service import BrowserTaskService, _Task

    class BrokenMetrics:
        enabled = True

        @staticmethod
        def begin(_record):
            raise OSError('simulated full disk')

    task_request = BrowserTaskRequest.from_payload({
        'schema_version': 1, 'task_id': 'failed-metrics',
        'session_key': ['owner', 'dashboard', 'session'], 'goal': 'research',
        'provider': 'gemini', 'scope_mode': 'public_research', 'expires_at': 1_900_000_000,
    })
    tracker = BudgetTracker(price_table(), ledger_path=tmp_path / 'budget.sqlite3')
    service = BrowserTaskService(metrics_store=BrokenMetrics(), budget_tracker=tracker)
    task = _Task(task_request)
    assert service._begin_metrics(task) is None

    with pytest.raises(PricingUnavailable, match='requires local metrics'):
        service._reserve_provider_call(task, {
            'provider': 'gemini', 'model': 'test-model', 'prompt': 'bounded',
            'system_prompt': 'bounded system prompt', 'max_output_tokens': 10,
            'attempt_id': 'attempt-id-00000010',
        })
