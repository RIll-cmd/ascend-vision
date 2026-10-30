from datetime import datetime, timedelta
import json

import pytest

from assistant.promises import PromiseCoach, PromiseStore, parse_promise

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


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class Voice:
    """The pacer stand-in: says each line at once (or holds it when ``hold``), then runs on_said."""

    def __init__(self, hold=False):
        self.said, self.held, self.hold, self.urgent = [], [], hold, []

    def __call__(self, text, on_said=None, urgent=False):
        if urgent:
            self.urgent.append(text)
        if self.hold:
            self.held.append((text, on_said))
            return
        self.said.append(text)
        if on_said:
            on_said()

    def release(self):
        for text, on_said in self.held:
            self.said.append(text)
            if on_said:
                on_said()
        self.held = []


def coach_for(tmp_path, now=WED_9AM, hold=False):
    clock, voice = Clock(now), Voice(hold)
    return PromiseCoach(PromiseStore(tmp_path / 'promises.json'), announce=voice, clock=clock), clock, voice


def test_explicit_capture_answers_and_implicit_capture_leaves_the_reply_to_the_ai(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    assert coach.intercept('remind me to call mom at 6') == "Okay. I'll check on call mom at 6."
    assert coach.intercept("Long day. I'll fix the login tomorrow.") is None
    assert voice.said == ["Noted: fix the login, tomorrow. Say 'drop that' if I got it wrong."]
    assert coach.intercept('remind me to call mom tomorrow') == "That's already on your list."
    saved = json.loads((tmp_path / 'promises.json').read_text())
    assert [p['what'] for p in saved] == ['call mom', 'fix the login']


def test_drop_that_removes_the_last_capture_only_shortly_after(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept("I'll fix the login tomorrow")
    assert coach.intercept('Drop that.') == 'Dropped.'
    assert coach.intercept('what did I promise?') == 'No open promises.'
    coach.intercept("I'll fix the login tomorrow")
    clock.now += timedelta(minutes=3)
    assert coach.intercept('drop that') is None


def test_the_ladder_asks_gently_then_firmer_then_offers_choices_then_goes_quiet(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to fix the login tomorrow')
    clock.now = datetime(2026, 10, 29, 9, 59)
    coach.tick(True)
    assert voice.said == []
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    coach.tick(True)  # one question at a time
    assert voice.said == ['Checking in on fix the login. Done?']
    clock.now = datetime(2026, 10, 30, 10, 0)
    coach.tick(True)
    assert voice.said[-1] == 'Fix the login is still open from Wednesday. Done yet?'
    for day, n in ((31, 3), (1, 4), (2, 5)):
        clock.now = datetime(2026, 10 if day == 31 else 11, day, 10, 0)
        coach.tick(True)
        assert voice.said[-1] == f'Fix the login has been open {n} days. Keep it, make it smaller, or drop it?'
    clock.now = datetime(2026, 11, 3, 10, 0)
    coach.tick(True)
    assert len(voice.said) == 5
    assert coach.intercept('what did I promise') == 'No open promises. Gone quiet: fix the login.'


@pytest.mark.parametrize('answer, reply, status, next_day', [
    ('Yes, done', 'Nice. Crossed off.', 'done', None),
    ('I did it', 'Nice. Crossed off.', 'done', None),
    ('not yet', "Okay. I'll ask again tomorrow.", 'open', datetime(2026, 10, 30, 10, 0)),
    ('nope', "Okay. I'll ask again tomorrow.", 'open', datetime(2026, 10, 30, 10, 0)),
    ('keep it', "Okay. I'll ask again in three days.", 'open', datetime(2026, 11, 1, 10, 0)),
    ('drop it', 'Dropped.', 'dropped', None),
])
def test_answers_to_a_check_in(tmp_path, answer, reply, status, next_day):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to fix the login tomorrow')
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    assert coach.intercept(answer) == reply
    saved = json.loads((tmp_path / 'promises.json').read_text())[0]
    assert saved['status'] == status
    if next_day:
        assert saved['next'] == next_day.isoformat()
    assert coach.intercept(answer) is None  # the window is closed


def test_making_it_smaller_replaces_the_promise(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to write the whole thesis tomorrow')
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    assert coach.intercept('make it smaller') == "What's the first small step?"
    assert coach.intercept('Just the outline.') == "Okay: the outline. I'll check on it in two hours."
    assert coach.intercept('what did I promise') == 'One open: the outline, due today at 12 pm.'


def test_window_opens_when_spoken_and_answers_outside_it_reach_the_ai(tmp_path):
    coach, clock, voice = coach_for(tmp_path, hold=True)
    coach.intercept('remind me to stretch in 5 minutes')
    clock.now += timedelta(minutes=5)
    coach.tick(True)
    clock.now += timedelta(minutes=10)  # still waiting behind other notices
    assert coach.intercept('yes') is None
    voice.release()
    assert voice.said == ['Checking in on stretch. Done?']
    assert coach.intercept('yes') == 'Nice. Crossed off.'
    coach.intercept('remind me to stretch in 5 minutes')
    clock.now += timedelta(minutes=5)
    coach.tick(True)
    voice.release()
    clock.now += timedelta(minutes=3)
    assert coach.intercept('yes') is None


def test_date_only_promises_wait_for_daytime_but_clock_times_do_not(tmp_path):
    coach, clock, voice = coach_for(tmp_path, now=datetime(2026, 10, 28, 21, 0))
    coach.intercept('remind me to pack tonight')  # 20:00 is gone, so due 22:00: outside daytime
    coach.intercept('remind me to take pills at 11pm')
    clock.now = datetime(2026, 10, 28, 23, 0)
    coach.tick(True)
    assert voice.said == ['Checking in on take pills. Done?']
    assert coach.intercept('yes') == 'Nice. Crossed off.'
    coach.tick(True)
    assert len(voice.said) == 1
    clock.now = datetime(2026, 10, 29, 8, 0)
    coach.tick(True)
    assert voice.said[-1] == 'Checking in on pack. Done?'


def test_only_a_reminder_set_for_a_clock_time_gets_past_focus(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to stretch in 5 minutes')
    coach.intercept('remind me to call mom tomorrow')
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    coach.intercept('not yet')
    coach.tick(True)
    assert voice.said == ['Checking in on stretch. Done?', 'Checking in on call mom. Done?']
    assert voice.urgent == ['Checking in on stretch. Done?']


def test_nothing_is_asked_while_the_owner_is_away(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to stretch in 5 minutes')
    clock.now += timedelta(minutes=6)
    coach.tick(False)
    assert voice.said == []
    coach.tick(True)
    assert voice.said == ['Checking in on stretch. Done?']


def test_done_with_crosses_off_the_closest_match(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to fix the login page tomorrow')
    coach.intercept('remind me to call mom tomorrow')
    assert coach.intercept("I'm done with the login") == 'Nice. Crossed off fix the login page.'
    assert coach.intercept('done with the dishes') is None
    assert coach.intercept('what did I promise') == 'One open: call mom, due tomorrow.'


def test_the_list_names_each_open_promise_with_its_day(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to call mom at 6:30pm')
    coach.intercept('remind me to fix the login on friday')
    assert coach.intercept('What are my promises') == (
        'Two open: call mom, due today at 6:30 pm; fix the login, due Friday.')


def asked(tmp_path, promise='remind me to fix the login tomorrow'):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept(promise)
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    return coach, clock, voice


def status(tmp_path):
    return [p['status'] for p in json.loads((tmp_path / 'promises.json').read_text())]


@pytest.mark.parametrize('chat', [
    'I did not', 'ok what is the weather', 'sure, but first tell me a joke', 'forget it, what time is it',
    'can you make the font smaller', 'no idea why this test fails, help?', 'keep going with the story',
])
def test_chat_inside_the_answer_window_reaches_the_ai_and_leaves_the_promise_alone(tmp_path, chat):
    coach, clock, voice = asked(tmp_path)
    assert coach.intercept(chat) is None
    assert status(tmp_path) == ['open']
    assert coach.intercept('done') == 'Nice. Crossed off.'


def test_a_question_after_smaller_is_not_taken_as_the_step(tmp_path):
    coach, clock, voice = asked(tmp_path)
    coach.intercept('smaller')
    assert coach.intercept('what is a closure?') is None
    assert coach.intercept('how do I start') is None
    assert coach.intercept('write the first test') == "Okay: write the first test. I'll check on it in two hours."


def test_done_with_and_the_list_only_as_short_commands(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to fix the login page tomorrow')
    assert coach.intercept("I'm done with the login error, now how do I deploy?") is None
    assert coach.intercept('tell me about my promises to my sister and how to keep them') is None
    assert status(tmp_path) == ['open']


@pytest.mark.parametrize('chat', [
    'remind me why we did that today', 'can you remind me what I said yesterday at 3',
    "I'm going to bed in an hour", 'I will see you tomorrow', "I'll call you back in 5 minutes",
    "I'll talk to you tomorrow", "I'm going to sleep tonight",
])
def test_ordinary_chat_is_not_a_promise(chat):
    assert parse_promise(chat, WED_9AM) is None


def test_a_clock_time_before_the_day_keeps_both():
    assert due("I'll message Sam at 10am tomorrow", datetime(2026, 10, 28, 8, 0)) == datetime(2026, 10, 29, 10, 0)
    assert due('remind me to call mom at 6 tomorrow') == datetime(2026, 10, 29, 18, 0)
    assert parse_promise('remind me to call mom at 6 tomorrow', WED_9AM).what == 'call mom'
    assert due('remind me to call mom tonight at 9') == datetime(2026, 10, 28, 21, 0)


def test_tomorrow_said_after_midnight_means_the_coming_day():
    assert due('remind me to call the bank tomorrow', datetime(2026, 10, 29, 0, 30)) == datetime(2026, 10, 29, 10, 0)


def test_the_second_sentence_is_read_when_the_first_is_not_a_task():
    assert parse_promise("I'll be late today. I'll fix the login tomorrow.", WED_9AM).what == 'fix the login'


@pytest.mark.parametrize('text', ['remind me to x y z in 99999999 days', 'remind me to stretch at 25',
                                  'remind me to stretch at 7:75'])
def test_impossible_times_are_not_caught(text):
    assert parse_promise(text, WED_9AM) is None


def test_a_hand_edited_bad_date_drops_that_promise_only(tmp_path):
    (tmp_path / 'promises.json').write_text(json.dumps([
        {'id': 'a', 'what': 'call mom', 'created': 'soon', 'next': 'later', 'timed': False},
        {'id': 'b', 'what': 'fix the login', 'created': WED_9AM.isoformat(), 'next': WED_9AM.isoformat(),
         'timed': False}]))
    coach, clock, voice = coach_for(tmp_path)
    coach.tick(True)
    assert voice.said == ['Checking in on fix the login. Done?']


def test_a_later_question_takes_the_answer(tmp_path):
    class Pacer(Voice):
        question_seq = 0

        def __call__(self, text, on_said=None, urgent=False):
            if on_said:
                self.question_seq += 1
            super().__call__(text, on_said, urgent)
    clock, voice = Clock(WED_9AM), Pacer()
    coach = PromiseCoach(PromiseStore(tmp_path / 'promises.json'), announce=voice, clock=clock)
    coach.intercept('remind me to stretch in 5 minutes')
    clock.now += timedelta(minutes=5)
    coach.tick(True)
    voice('Lately I have noticed fewer commits. Want a small plan for today?', on_said=lambda: None)
    assert coach.intercept('yes') is None, 'the slump question was asked last'


def test_drop_that_counts_from_when_the_note_is_spoken(tmp_path):
    coach, clock, voice = coach_for(tmp_path, hold=True)
    coach.intercept("I'll fix the login tomorrow")
    clock.now += timedelta(minutes=3)
    voice.release()
    assert coach.intercept('drop that') == 'Dropped.'


def test_a_broken_file_is_set_aside_and_closed_promises_go_after_thirty_days(tmp_path):
    path = tmp_path / 'promises.json'
    path.write_text('{not json')
    coach, clock, voice = coach_for(tmp_path)
    assert (tmp_path / 'promises.json.bad').exists()
    coach.intercept('remind me to stretch in 5 minutes')
    clock.now += timedelta(minutes=5)
    coach.tick(True)
    coach.intercept('yes')
    clock.now += timedelta(days=31)
    coach.intercept('remind me to stretch in 5 minutes')
    assert [p['status'] for p in json.loads(path.read_text())] == ['open']
