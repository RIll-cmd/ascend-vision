import pytest

from browser.contracts import BrowserDecision, BrowserTaskRequest, ContractError


def test_task_request_normalizes_goal_and_rejects_unknown_fields():
    request = BrowserTaskRequest.from_payload({
        'schema_version': 1,
        'task_id': 'task-1',
        'session_key': ['owner-1', 'dashboard', 'session-1'],
        'goal': '  Find the official docs  ',
        'provider': 'gemini',
        'scope_mode': 'public_research',
        'expires_at': 1_800_000_000,
    })

    assert request.goal == 'Find the official docs'
    assert request.session_key == ('owner-1', 'dashboard', 'session-1')

    payload = request.to_payload()
    payload['owner_id'] = 'attacker'
    with pytest.raises(ContractError, match='unknown'):
        BrowserTaskRequest.from_payload(payload)


@pytest.mark.parametrize('change', [
    {'schema_version': 2},
    {'session_key': ['owner-1', 'discord_dm', 'session-1']},
    {'session_key': ['owner-1', 'dashboard']},
    {'goal': 'x' * 4001},
    {'provider': 'other'},
    {'provider': ['gemini']},
    {'scope_mode': 'selected_origins'},
    {'scope_mode': []},
    {'session_key': ['owner-1', ['dashboard'], 'session-1']},
    {'expires_at': float('nan')},
])
def test_task_request_rejects_untrusted_identity_or_out_of_scope_values(change):
    payload = {
        'schema_version': 1,
        'task_id': 'task-1',
        'session_key': ['owner-1', 'dashboard', 'session-1'],
        'goal': 'Find official docs',
        'provider': 'gemini',
        'scope_mode': 'public_research',
        'expires_at': 1_800_000_000,
    }
    payload.update(change)

    with pytest.raises(ContractError):
        BrowserTaskRequest.from_payload(payload)


def test_browser_decision_rejects_arbitrary_code_and_extra_fields():
    with pytest.raises(ContractError, match='action'):
        BrowserDecision.from_payload({
            'schema_version': 1,
            'observation_id': 'obs-1',
            'action': 'evaluate',
            'arguments': {'code': 'document.cookie'},
        })

    with pytest.raises(ContractError, match='unknown'):
        BrowserDecision.from_payload({
            'schema_version': 1,
            'observation_id': 'obs-1',
            'action': 'observe',
            'arguments': {},
            'authorization': True,
        })


def test_browser_decision_only_accepts_action_specific_bounded_arguments():
    decision = BrowserDecision.from_payload({
        'schema_version': 1,
        'observation_id': 'obs-1',
        'action': 'navigate',
        'arguments': {'url': 'https://example.org/docs'},
    })
    assert decision.action == 'navigate'

    with pytest.raises(ContractError):
        BrowserDecision.from_payload({
            'schema_version': 1,
            'observation_id': 'obs-1',
            'action': 'navigate',
            'arguments': {'url': 'file:///C:/secret.txt'},
        })


@pytest.mark.parametrize('change', [
    {'action': []},
    {'action': 'scroll', 'arguments': {'direction': [], 'pixels': 10}},
])
def test_browser_decision_malformed_unhashable_values_raise_contract_error(change):
    payload = {
        'schema_version': 1,
        'observation_id': 'obs-1',
        'action': 'observe',
        'arguments': {},
    }
    payload.update(change)
    with pytest.raises(ContractError):
        BrowserDecision.from_payload(payload)
