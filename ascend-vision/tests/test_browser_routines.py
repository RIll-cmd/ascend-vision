import json
from pathlib import Path
import time

import pytest

from browser.contracts import BrowserTaskRequest
from browser.executor import BrowserObservation
from browser.policy import BrowserPolicy, PolicyDenied
from browser.routines.contracts import RoutineContractError, RoutineInvocation, parse_definition
from browser.routines.registry import RoutineRegistry
from browser.routines.verifiers import verify_completion


DEFINITIONS = Path(__file__).parents[1] / 'browser' / 'routines' / 'definitions'


def request():
    return BrowserTaskRequest.from_payload({
        'schema_version': 1, 'task_id': 'routine-task',
        'session_key': ['owner', 'dashboard', 'session'],
        'goal': 'ignored caller goal', 'provider': 'gemini',
        'scope_mode': 'public_research', 'expires_at': 1_900_000_000,
    })


def test_manifest_rejects_unknown_fields():
    payload = json.loads((DEFINITIONS / 'docs_brief_v1.json').read_text(encoding='utf-8'))
    payload['unexpected_grant'] = True
    with pytest.raises(RoutineContractError, match='unknown or missing fields'):
        parse_definition(payload)


def test_candidate_is_not_enabled_until_exact_digest_is_accepted():
    registry = RoutineRegistry(DEFINITIONS)
    digest = registry.digest('docs_brief', 1)
    with pytest.raises(RoutineContractError, match='disabled'):
        registry.resolve('docs_brief', 1, digest)

    accepted = RoutineRegistry(DEFINITIONS, enabled_versions=(('docs_brief', 1, digest),))
    invocation = RoutineInvocation.from_payload({
        'routine_id': 'docs_brief', 'version': 1, 'digest': digest,
        'inputs': {'url': 'https://playwright.dev/python/docs/locators', 'question': 'What are locators?'},
    })
    prepared = accepted.prepare(invocation, request(), base_policy=BrowserPolicy())
    assert prepared.budget.seconds <= 120
    assert not prepared.policy.allows_action('fill', target_kind='text_field')
    with pytest.raises(PolicyDenied):
        prepared.policy.validate_url('https://playwright.dev/python/docsx/locators')


def test_two_document_verifier_requires_both_observed_sources():
    registry = RoutineRegistry(DEFINITIONS, enabled_versions=(
        ('docs_compare', 1, RoutineRegistry(DEFINITIONS).digest('docs_compare', 1)),
    ))
    digest = registry.digest('docs_compare', 1)
    invocation = RoutineInvocation.from_payload({
        'routine_id': 'docs_compare', 'version': 1, 'digest': digest,
        'inputs': {'url_a': 'https://playwright.dev/python/docs/locators',
                   'url_b': 'https://playwright.dev/python/docs/intro', 'question': 'Compare'},
    })
    prepared = registry.prepare(invocation, request(), base_policy=BrowserPolicy())
    observations = (
        BrowserObservation('routine-task', 'page-1', 1, 'obs-one', '2026-09-29T00:00:00Z',
                           invocation.inputs['url_a'], 'Locators', 'Visible locator text', (), False),
        BrowserObservation('routine-task', 'page-2', 1, 'obs-two', '2026-09-29T00:01:00Z',
                           invocation.inputs['url_b'], 'Intro', 'Visible intro text', (), False),
    )
    result = {'source_observation_ids': ['obs-one', 'obs-two'],
              'sources': [invocation.inputs['url_a'], invocation.inputs['url_b']],
              'findings': [{'text': 'The pages explain different topics.'}]}

    passed = verify_completion('two_documents_v1', prepared, observations, result)
    missing_second_citation = verify_completion('two_documents_v1', prepared, observations,
                                                {**result, 'source_observation_ids': ['obs-one']})
    foreign_reference = verify_completion('two_documents_v1', prepared, observations,
                                          {**result, 'source_observation_ids': ['obs-one', 'foreign']})

    assert passed.verdict == 'passed'
    assert missing_second_citation.verdict == 'failed'
    assert foreign_reference.verdict == 'failed'


def test_routine_disable_state_survives_restart(tmp_path):
    first = RoutineRegistry(DEFINITIONS, enabled_versions=(
        ('docs_brief', 1, RoutineRegistry(DEFINITIONS).digest('docs_brief', 1)),
    ), disabled_path=tmp_path / 'disabled.json')
    first.disable('docs_brief', 1)
    restarted = RoutineRegistry(DEFINITIONS, enabled_versions=(
        ('docs_brief', 1, first.digest('docs_brief', 1)),
    ), disabled_path=tmp_path / 'disabled.json')

    assert not next(item for item in restarted.catalogue() if item['routine_id'] == 'docs_brief')['enabled']


def test_two_page_routine_uses_the_broker_and_passes_evidence_verifier(monkeypatch, caplog):
    import logging
    from browser import policy as policy_module
    from browser.service import BrowserTaskService
    caplog.set_level(logging.INFO, logger='browser.service')

    first_url = 'https://playwright.dev/python/docs/locators'
    second_url = 'https://playwright.dev/python/docs/intro'
    original_policy = policy_module.BrowserPolicy
    monkeypatch.setattr(policy_module, 'BrowserPolicy',
                        lambda: original_policy(allow_test_origins={'https://playwright.dev'}))
    registry = RoutineRegistry(DEFINITIONS)
    digest = registry.digest('docs_compare', 1)
    registry = RoutineRegistry(DEFINITIONS, enabled_versions=(('docs_compare', 1, digest),))

    class FixtureExecutor:
        def __init__(self, policy):
            self.policy = policy
            self.current = BrowserObservation('routine-task', 'page-blank', 0, 'obs-blank',
                '2026-09-29T00:00:00Z', 'about:blank', '', '', (), False)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def observe(self, task_id):
            return self.current

        def navigate(self, task_id, url):
            self.policy.validate_url(url)
            number = 1 if url == first_url else 2
            self.current = BrowserObservation(task_id, f'page-{number}', 1, f'obs-{number}',
                f'2026-09-29T00:0{number}:00Z', url, f'Document {number}',
                f'Visible documentation body {number}.', (), False)
            return self.current

    def executor_factory(task, *, policy_override, max_pages):
        assert max_pages <= 3
        return FixtureExecutor(policy_override)

    def planner(task, current, **kwargs):
        assert kwargs['routine'].definition.routine_id == 'docs_compare'
        assert len(kwargs['routine_observations']) <= 3
        if current.url == 'about:blank':
            action, args = 'navigate', {'url': first_url}
        elif current.url == first_url:
            action, args = 'navigate', {'url': second_url}
        else:
            action, args = 'finish', {
                'finding': 'The documents cover distinct topics.',
                'source_observation_ids': ['obs-1', 'obs-2'],
            }
        return {'schema_version': 1, 'observation_id': current.observation_id,
                'action': action, 'arguments': args}

    service = BrowserTaskService(
        executor_factory_for_task=executor_factory,
        decision_provider=planner,
        decision_provider_with_usage=planner,
        routine_registry=registry,
    )
    try:
        service.start()
        receipt = service.submit_routine(request(), {
            'routine_id': 'docs_compare', 'version': 1, 'digest': digest,
            'inputs': {'url_a': first_url, 'url_b': second_url, 'question': 'Compare topics'},
        })
        deadline = time.monotonic() + 5
        page = None
        while time.monotonic() < deadline:
            page = service.events(receipt.task_id, 0, ('owner', 'dashboard', 'session'))
            if page.state in {'completed', 'failed', 'partial', 'cancelled', 'unknown'}:
                break
            time.sleep(.01)
        assert page.state == 'completed', {
            'events': [(event.state, event.summary) for event in page.events],
            'logs': [record.getMessage() for record in caplog.records],
        }
        assert page.result['verification']['verdict'] == 'passed'
        assert page.result['source_observation_ids'] == ['obs-1', 'obs-2']
    finally:
        service.close()


def test_disabling_a_queued_routine_cancels_it_and_blocks_reuse(monkeypatch):
    import threading
    from browser import policy as policy_module
    from browser.service import BrowserTaskService

    original_policy = policy_module.BrowserPolicy
    monkeypatch.setattr(policy_module, 'BrowserPolicy',
                        lambda: original_policy(allow_test_origins={'https://playwright.dev'}))
    registry = RoutineRegistry(DEFINITIONS)
    digest = registry.digest('docs_brief', 1)
    registry = RoutineRegistry(DEFINITIONS, enabled_versions=(('docs_brief', 1, digest),))
    invocation = {
        'routine_id': 'docs_brief', 'version': 1, 'digest': digest,
        'inputs': {'url': 'https://playwright.dev/python/docs/locators', 'question': 'Describe locators'},
    }
    entered = threading.Event()
    release = threading.Event()

    class FixtureExecutor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def observe(self, task_id):
            return BrowserObservation(task_id, 'page', 0, 'blocker-observation',
                '2026-09-29T00:00:00Z', 'about:blank', '', '', (), False)

    def planner(task, _observation):
        if task.task_id == 'blocker':
            entered.set()
            assert release.wait(3)
        return {'schema_version': 1, 'observation_id': 'blocker-observation',
                'action': 'ask_user', 'arguments': {'question': 'fixture'}}

    service = BrowserTaskService(
        executor_factory=lambda: FixtureExecutor(), decision_provider=planner,
        routine_registry=registry,
    )
    try:
        service.start()
        service.submit(BrowserTaskRequest.from_payload({
            'schema_version': 1, 'task_id': 'blocker',
            'session_key': ['owner', 'dashboard', 'session'], 'goal': 'hold the worker',
            'provider': 'gemini', 'scope_mode': 'public_research', 'expires_at': 1_900_000_000,
        }))
        assert entered.wait(2)
        receipt = service.submit_routine(request(), invocation)
        service.disable_routine('docs_brief', 1)
        page = service.events(receipt.task_id, 0, ('owner', 'dashboard', 'session'))
        assert page.state == 'cancelled'
        with pytest.raises(RoutineContractError, match='disabled'):
            service.submit_routine(request(), invocation)
    finally:
        release.set()
        service.close()
