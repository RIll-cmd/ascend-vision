import time

import pytest

from browser.authorization import ActionProposal, GrantBook


def proposal(**changes):
    fields = {
        'task_id': 'task-1',
        'action_id': 'action-1',
        'owner': 'owner-1',
        'observation_id': 'observation-1',
        'document_revision': 4,
        'origin': 'https://example.org',
        'action': 'fill',
        'target_ref': 'field-1',
        'target_label': 'Message',
        'arguments': {'value': 'Hello'},
        'expected_effect': 'Set the Message field to Hello',
        'expires_at': time.time() + 30,
    }
    fields.update(changes)
    return ActionProposal(**fields)


def test_grant_is_bound_to_the_reviewed_proposal_and_can_be_used_once():
    book = GrantBook()
    reviewed = proposal()
    book.register(reviewed)

    grant = book.approve('task-1', 'action-1', reviewed.digest, 'owner-1')

    assert grant is not None
    assert book.consume(reviewed, owner='owner-1') is True
    assert book.consume(reviewed, owner='owner-1') is False


def test_grant_rejects_changed_arguments_and_wrong_owner():
    book = GrantBook()
    reviewed = proposal()
    book.register(reviewed)
    book.approve('task-1', 'action-1', reviewed.digest, 'owner-1')

    assert book.consume(proposal(arguments={'value': 'Different'}), owner='owner-1') is False
    assert book.consume(reviewed, owner='other-owner') is False


def test_expired_proposals_cannot_be_approved_or_consumed():
    book = GrantBook(clock=lambda: 100.0)
    expired = proposal(expires_at=99.0)
    book.register(expired)

    assert book.approve('task-1', 'action-1', expired.digest, 'owner-1') is None
    assert book.consume(expired, owner='owner-1') is False


def test_proposal_digest_changes_when_origin_or_page_revision_changes():
    base = proposal()

    assert proposal(origin='https://other.example').digest != base.digest
    assert proposal(document_revision=5).digest != base.digest
    assert proposal(arguments={'value': 'Goodbye'}).digest != base.digest


def test_proposal_rejects_unbounded_or_malformed_content():
    with pytest.raises(ValueError):
        proposal(action='evaluate')
    with pytest.raises(ValueError):
        proposal(arguments={'value': 'x' * 3000})
