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
    clock.now += timedelta(minutes=4)
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


def test_the_note_is_urgent_opens_no_question_and_drop_that_lasts_three_minutes(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    voice.question_seq = 0
    coach.intercept("I'll fix the login tomorrow")
    assert voice.urgent == ["Noted: fix the login, tomorrow. Say 'drop that' if I got it wrong."]
    clock.now += timedelta(minutes=2, seconds=50)
    assert coach.intercept('drop that') == 'Dropped.'


def test_a_held_ordinary_ask_does_not_block_a_clock_time_reminder(tmp_path):
    coach, clock, voice = coach_for(tmp_path, hold=True)
    coach.intercept("I'll do the laundry today")
    coach.intercept('remind me to call mom at 6pm')
    clock.now = datetime(2026, 10, 28, 17, 0)
    coach.tick(True)  # the laundry ask waits behind a focus block
    clock.now = datetime(2026, 10, 28, 18, 1)
    coach.tick(True)
    assert voice.urgent[-1] == 'Checking in on call mom. Done?'


def test_other_helpers_can_add_a_promise(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    assert coach.add('get back to branch fix login in ascend hub', datetime(2026, 10, 29, 10, 0))
    assert not coach.add('get back to branch fix login in ascend hub', datetime(2026, 10, 30, 10, 0))
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    assert voice.said == ['Checking in on get back to branch fix login in ascend hub. Done?']


def test_a_bare_okay_does_not_cross_off(tmp_path):
    coach, clock, voice = asked(tmp_path)
    assert coach.intercept('okay') is None
    assert coach.intercept('yes') == 'Nice. Crossed off.'


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


# Gap 1: evidence, Room queue, phone

from assistant.promises import find_evidence


def test_evidence_matches_two_shared_words_or_the_only_word():
    commits = [('ascend_hub', 'docs: tidy readme'), ('ascend_hub', 'fix(hub): login redirect loop')]
    assert find_evidence('fix the login', commits, []) == "the commit 'fix(hub): login redirect loop' in ascend_hub"
    assert find_evidence('the dentist', [('x', 'Dentist booking notes')], []) == "the commit 'Dentist booking notes' in x"
    assert find_evidence('fix the login', [('x', 'login page colors')], []) is None  # one of two words
    assert find_evidence('write the report', [], ['Write the weekly report']) == "the Room session 'Write the weekly report'"
    assert find_evidence('go', [('x', 'go go go')], []) is None  # no word long enough to judge


def ask_with_evidence(tmp_path, evidence):
    clock, voice = Clock(WED_9AM), Voice(False)
    coach = PromiseCoach(PromiseStore(tmp_path / 'promises.json'), announce=voice, clock=clock, evidence=evidence)
    coach.intercept('remind me to fix the login tomorrow')
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    return coach, clock, voice




def test_first_check_in_mentions_the_commit_it_found(tmp_path):
    seen = []

    def evidence(what, since):
        seen.append((what, since))
        return "the commit 'fix login redirect' in ascend_hub"

    coach, clock, voice = ask_with_evidence(tmp_path, evidence)
    assert voice.said == ["Looks like fix the login is done: I saw the commit 'fix login redirect' in ascend_hub. "
                          'Cross it off?']
    assert seen == [('fix the login', WED_9AM)]
    assert coach.intercept('yes') == 'Nice. Crossed off.'


def test_evidence_is_only_looked_for_on_the_first_ask_and_failures_fall_back(tmp_path):
    calls = []

    def evidence(what, since):
        calls.append(what)
        raise OSError('git missing')

    coach, clock, voice = ask_with_evidence(tmp_path, evidence)
    assert voice.said == ['Checking in on fix the login. Done?']
    clock.now = datetime(2026, 10, 30, 10, 0)
    coach.tick(True)
    assert len(calls) == 1


def queue_coach(tmp_path, queue):
    clock, voice = Clock(WED_9AM), Voice(False)
    coach = PromiseCoach(PromiseStore(tmp_path / 'promises.json'), announce=voice, clock=clock, queue=queue)
    coach.intercept('remind me to fix the login tomorrow')
    for day in (29, 30, 31):  # the Room is offered from the third ask
        clock.now = datetime(2026, 10, day, 10, 0)
        coach.tick(True)
    return coach, clock, voice


@pytest.mark.parametrize('answer', ['queue it', 'Queue it in the Room.', 'send it to the room', 'room'])
def test_queue_it_hands_the_promise_to_the_room(tmp_path, answer):
    asked = []
    coach, clock, voice = queue_coach(tmp_path, lambda what: (asked.append(what), (True, 'Queued in the Agent Room.'))[1])
    assert coach.intercept(answer) == 'Queued in the Agent Room.'
    assert asked == ['fix the login']
    assert json.loads((tmp_path / 'promises.json').read_text())[0]['status'] == 'queued'
    assert coach.intercept('what did I promise') == 'No open promises.'


def test_a_failed_queue_keeps_the_promise_open(tmp_path):
    coach, clock, voice = queue_coach(tmp_path, lambda what: (False, "I couldn't reach the agent room."))
    assert coach.intercept('queue it') == "I couldn't reach the agent room."
    assert json.loads((tmp_path / 'promises.json').read_text())[0]['status'] == 'open'


def test_without_a_room_queue_the_answer_is_left_to_the_ai(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to fix the login tomorrow')
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    assert coach.intercept('queue it') is None


def test_the_firm_question_offers_the_room_when_there_is_one(tmp_path):
    coach, clock, voice = queue_coach(tmp_path, lambda what: (True, 'ok'))
    assert voice.said[-1] == ('Fix the login has been open 3 days. '
                              'Keep it, make it smaller, queue it in the Room, or drop it?')


def test_phone_takes_explicit_reminders_and_the_list_without_speaking(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    assert coach.phone('remind me to call mom at 6') == "Okay. I'll check on call mom at 6."
    assert coach.phone("I'll fix the login tomorrow") is None
    assert coach.phone('what did I promise?') == 'One open: call mom, due today at 6 pm.'
    assert coach.phone('how are you') is None
    assert voice.said == []


def test_make_evidence_reads_owner_commits_since_the_promise_and_done_room_sessions(tmp_path):
    from assistant.promises import make_evidence
    calls = []

    def run_git(repo, *args):
        calls.append((repo.name, args))
        if args[:2] == ('config', 'user.email'):
            return 'me@example.com\n' if repo.name == 'hub' else None
        return 'fix(hub): login redirect\n' if repo.name == 'hub' else ''

    sessions = tmp_path / 'room' / 'sessions'
    for sid, stage, created, task in (('1761000000000-aaaaaa', 'done', '2026-10-28T12:00:00Z', 'Write the weekly report\nmore'),
                                      ('1761000000001-bbbbbb', 'stopped', '2026-10-28T12:00:00Z', 'Plan the trip'),
                                      ('1761000000002-cccccc', 'done', '2026-10-01T12:00:00Z', 'Plan the trip')):
        (sessions / sid).mkdir(parents=True)
        (sessions / sid / 'state.json').write_text(json.dumps({'stage': stage, 'createdAt': created, 'task': task}))
    lookup = make_evidence([tmp_path / 'hub', tmp_path / 'other'], run_git, tmp_path / 'room')
    assert lookup('fix the login', WED_9AM - timedelta(days=1)) == "the commit 'fix(hub): login redirect' in hub"
    log = [args for name, args in calls if args[0] == 'log']
    assert log[0] == ('log', '--all', '--no-merges', '--since=2026-10-27T09:00:00', '--author=me@example' + chr(92) + '.com',
                      '--format=%s', '-n', '50')
    assert len(log) == 1  # no email set in the other repo: nobody's commits can be told apart, so none count
    assert lookup('write the report', WED_9AM - timedelta(days=1)) == "the Room session 'Write the weekly report'"
    assert lookup('plan the trip', WED_9AM - timedelta(days=1)) is None  # stopped, or made before the promise
    assert make_evidence([], run_git, tmp_path / 'missing')('fix the login', WED_9AM) is None


def test_room_titles_are_the_first_sentence_cut_at_a_word(tmp_path):
    from assistant.promises import make_evidence
    folder = tmp_path / 'room' / 'sessions' / '1761000000000-aaaaaa'
    folder.mkdir(parents=True)
    task = 'Make a one-page HTML stopwatch with start, stop and reset. At the end, propose one more idea.'
    (folder / 'state.json').write_text(json.dumps({'stage': 'done', 'createdAt': '2026-10-28T12:00:00Z', 'task': task}))
    found = make_evidence([], None, tmp_path / 'room')('make a stopwatch', WED_9AM - timedelta(days=1))
    assert found == "the Room session 'Make a one-page HTML stopwatch with start, stop and reset'"
    long = 'word ' * 30
    (folder / 'state.json').write_text(json.dumps({'stage': 'done', 'createdAt': '2026-10-28T12:00:00Z', 'task': long}))
    found = make_evidence([], None, tmp_path / 'room')('word', WED_9AM - timedelta(days=1))
    assert found == "the Room session '" + ' '.join(['word'] * 16) + "'"


def test_queue_it_is_only_taken_once_the_room_was_offered(tmp_path):
    clock, voice = Clock(WED_9AM), Voice(False)
    coach = PromiseCoach(PromiseStore(tmp_path / 'promises.json'), announce=voice, clock=clock,
                         queue=lambda what: (True, 'Queued.'))
    coach.intercept('remind me to call mom tomorrow')
    clock.now = datetime(2026, 10, 29, 10, 0)
    coach.tick(True)
    assert coach.intercept('room') is None and coach.intercept('queue it') is None


def test_the_room_is_called_without_holding_the_coach(tmp_path):
    import threading
    got = []

    def queue(what):
        def try_lock():
            took = coach._lock.acquire(timeout=0.5)
            got.append(took)
            if took:
                coach._lock.release()
        other = threading.Thread(target=try_lock)
        other.start()
        other.join()
        return True, 'Queued.'

    coach, clock, voice = queue_coach(tmp_path, lambda what: (True, 'x'))
    coach.queue = queue
    assert coach.intercept('queue it') == 'Queued.'
    assert got == [True]


def test_evidence_ignores_commit_prefixes_and_repo_names():
    commits = [('ascend-vision', 'fix(vision): read the right file')]
    assert find_evidence('fix the vision mic bug', commits, []) is None
    assert find_evidence('fix the vision mic bug', [('ascend-vision', 'fix(vision): mic bug crackle')], []) == (
        "the commit 'fix(vision): mic bug crackle' in ascend-vision")
    assert find_evidence("answer bob's email", [('x', "Answer Bob's email about rent")], []) is not None


def test_phone_reminders_never_answer_a_question_open_at_the_pc(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept('remind me to email bob tomorrow')
    clock.now = datetime(2026, 10, 29, 10, 0)
    for day in (29, 30, 31):
        clock.now = datetime(2026, 10, day, 10, 0)
        coach.tick(True)
    assert coach.intercept('smaller') == "What's the first small step?"
    assert coach.phone('remind me to buy milk tomorrow') == "Okay. I'll check on buy milk tomorrow."
    assert coach.intercept('open the draft') == "Okay: open the draft. I'll check on it in two hours."
    saved = {p['what']: p['status'] for p in json.loads((tmp_path / 'promises.json').read_text())}
    assert saved == {'email bob': 'dropped', 'buy milk': 'open', 'open the draft': 'open'}
    assert coach.phone('remind me to buy milk tomorrow') == "That's already on your list."


def test_a_phone_reminder_keeps_the_pc_drop_that_window(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    coach.intercept("I'll fix the login tomorrow")
    coach.phone('remind me to buy milk tomorrow')
    assert coach.intercept('drop that') == 'Dropped.'
    assert {p['what']: p['status'] for p in json.loads((tmp_path / 'promises.json').read_text())} == {
        'fix the login': 'dropped', 'buy milk': 'open'}


# Polish: bare weekdays, a reminder with no task yet, the Discord bot's desk, careful authors

@pytest.mark.parametrize('when, expected', [
    ('friday', datetime(2026, 10, 30, 10, 0)),
    ('next friday', datetime(2026, 10, 30, 10, 0)),
    ('this friday', datetime(2026, 10, 30, 10, 0)),
])
def test_a_bare_weekday_is_a_time(when, expected):
    assert due(f'remind me {when} to stretch') == expected
    assert due(f'remind me to stretch {when}') == expected


def test_a_reminder_without_a_task_asks_for_it(tmp_path):
    coach, clock, voice = coach_for(tmp_path)
    assert coach.intercept('Remind me Friday.') == 'What should I remind you about on friday?'
    assert coach.intercept('what time is it?') is None  # a question is chat, the offer stays
    assert coach.intercept('to call the landlord') == "Okay. I'll check on call the landlord on friday."
    saved = json.loads((tmp_path / 'promises.json').read_text())
    assert [(p['what'], p['next']) for p in saved] == [('call the landlord', '2026-10-30T10:00:00')]
    coach.intercept('remind me tomorrow')
    clock.now += timedelta(minutes=3)
    assert coach.intercept('water the plants') is None  # too late: it was not an answer


def test_the_discord_bots_desk_leaves_reminders_for_vision(tmp_path):
    from assistant.promises import promise_desk
    desk = promise_desk(tmp_path, clock=lambda: WED_9AM)
    assert desk('remind me to call mom tomorrow') == "Okay. I'll check on call mom tomorrow."
    assert desk('remind me to call mom tomorrow') == "That's already on your list."
    assert desk("I'll fix the login tomorrow") is None
    assert desk('how are you') is None
    clock, voice = Clock(WED_9AM), Voice(False)
    coach = PromiseCoach(PromiseStore(tmp_path / 'promises.json'), announce=voice, clock=clock,
                         inbox=tmp_path / 'promise_inbox.jsonl')
    coach.tick(False)  # the inbox is read even while the owner is away
    assert coach.intercept('what did I promise') == 'One open: call mom, due tomorrow.'
    assert not (tmp_path / 'promise_inbox.jsonl').exists()
    assert desk('what did I promise') == 'One open: call mom, due tomorrow.'
    with open(tmp_path / 'promise_inbox.jsonl', 'a', encoding='utf-8') as f:
        f.write('not json\n{"what": 3}\n')
    coach.tick(False)
    assert len(json.loads((tmp_path / 'promises.json').read_text())) == 1


def test_an_email_with_regex_characters_is_matched_literally(tmp_path):
    from assistant.promises import make_evidence
    calls = []

    def run_git(repo, *args):
        calls.append(args)
        return 'me+vision@example.com\n' if args[0] == 'config' else ''
    make_evidence([tmp_path / 'r'], run_git, tmp_path / 'room')('fix the login', WED_9AM)
    assert any(a.startswith('--author=') and a.endswith('me\\+vision@example\\.com') for a in calls[-1])
