from datetime import datetime

import pytest

from assistant.promises import parse_promise

WED_9AM = datetime(2026, 10, 28, 9, 0)  # a Wednesday


def due(text, now=WED_9AM):
    caught = parse_promise(text, now)
    return caught.due if caught else None


def test_explicit_reminders_in_both_orders():
    caught = parse_promise('Hey Vision, remind me to call mom tomorrow.', WED_9AM)
    assert (caught.what, caught.when, caught.explicit) == ('call mom', 'tomorrow', True)
    assert caught.due == datetime(2026, 10, 29, 10, 0) and not caught.timed
    caught = parse_promise('remind me tomorrow to water the plants', WED_9AM)
    assert (caught.what, caught.due) == ('water the plants', datetime(2026, 10, 29, 10, 0))
    assert parse_promise('remind me about the dentist on friday', WED_9AM).what == 'the dentist'


def test_implicit_promises_inside_a_longer_message():
    caught = parse_promise('Ugh, long day. I will fix the login tomorrow and then sleep!', WED_9AM)
    assert (caught.what, caught.when, caught.explicit) == ('fix the login', 'tomorrow', False)
    assert parse_promise("I'm going to finish the report by friday", WED_9AM).what == 'finish the report'
    assert parse_promise("i'll do it. Tomorrow I'm busy", WED_9AM) is None  # the time is in another sentence


@pytest.mark.parametrize('when, expected', [
    ('today', datetime(2026, 10, 28, 17, 0)),
    ('tonight', datetime(2026, 10, 28, 20, 0)),
    ('this afternoon', datetime(2026, 10, 28, 15, 0)),
    ('this evening', datetime(2026, 10, 28, 19, 0)),
    ('tomorrow morning', datetime(2026, 10, 29, 10, 0)),
    ('tomorrow afternoon', datetime(2026, 10, 29, 15, 0)),
    ('tomorrow evening', datetime(2026, 10, 29, 19, 0)),
    ('tomorrow night', datetime(2026, 10, 29, 20, 0)),
    ('on friday', datetime(2026, 10, 30, 10, 0)),
    ('by wednesday', datetime(2026, 11, 4, 10, 0)),  # said on a Wednesday: next week's
    ('next week', datetime(2026, 11, 2, 10, 0)),
    ('in 20 minutes', datetime(2026, 10, 28, 9, 20)),
    ('in an hour', datetime(2026, 10, 28, 10, 0)),
    ('in 2 days', datetime(2026, 10, 30, 9, 0)),
    ('at 10', datetime(2026, 10, 28, 10, 0)),
    ('at 6', datetime(2026, 10, 28, 18, 0)),  # no am/pm: the next six o'clock
    ('at 8am', datetime(2026, 10, 29, 8, 0)),  # already passed today
    ('at 3:30pm', datetime(2026, 10, 28, 15, 30)),
    ('at 14:15', datetime(2026, 10, 28, 14, 15)),
    ('tomorrow at 3pm', datetime(2026, 10, 29, 15, 0)),
])
def test_when_forms(when, expected):
    assert due(f'remind me to stretch {when}') == expected


def test_timed_only_for_clock_times_and_durations():
    assert parse_promise('remind me to stretch at 6', WED_9AM).timed
    assert parse_promise('remind me to stretch in 5 minutes', WED_9AM).timed
    assert not parse_promise('remind me to stretch tonight', WED_9AM).timed


def test_a_time_of_day_already_gone_moves_to_an_hour_from_now():
    assert due('I will email Sam today', datetime(2026, 10, 28, 18, 0)) == datetime(2026, 10, 28, 19, 0)


def test_nothing_without_a_time_or_with_a_silly_task():
    assert parse_promise("I'll fix the login", WED_9AM) is None
    assert parse_promise('remind me to', WED_9AM) is None
    assert parse_promise('I will be busy today', WED_9AM) is None
    assert parse_promise(f"I'll {'x' * 130} tomorrow", WED_9AM) is None
    assert parse_promise('What is the weather today?', WED_9AM) is None
