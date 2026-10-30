from assistant.announcement_pacer import AnnouncementPacer


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_notices_that_arrive_together_are_spoken_one_at_a_time_with_a_gap():
    said, clock = [], Clock()
    pace = AnnouncementPacer(said.append, gap_seconds=45, clock=clock)
    pace('Good morning! It may rain today.')
    pace("You're 2 commits behind your average.")
    pace('Water break? You are at 1 of 8 glasses.')
    assert said == ['Good morning! It may rain today.'], 'the first notice is not delayed'

    clock.now += 44
    pace.pump()
    assert len(said) == 1
    clock.now += 1
    pace.pump()
    assert said[-1] == "You're 2 commits behind your average."
    clock.now += 45
    pace.pump()
    pace.pump()
    assert said[-1] == 'Water break? You are at 1 of 8 glasses.'
    assert len(said) == 3


def test_a_notice_already_waiting_is_not_queued_twice():
    said, clock = [], Clock()
    pace = AnnouncementPacer(said.append, gap_seconds=45, clock=clock)
    pace('first')
    pace('The agents need you to review the plan.')
    pace('The agents need you to review the plan.')
    clock.now += 90
    pace.pump()
    pace.pump()
    assert said == ['first', 'The agents need you to review the plan.']


def test_a_failing_speaker_does_not_block_the_next_notice():
    said, clock = [], Clock()

    def say(text):
        if text == 'broken':
            raise RuntimeError('speaker failed')
        said.append(text)

    pace = AnnouncementPacer(say, gap_seconds=10, clock=clock)
    pace('broken')
    pace('next')
    clock.now += 10
    pace.pump()
    assert said == ['next']


def test_on_said_runs_when_the_notice_is_actually_spoken():
    from assistant.announcement_pacer import AnnouncementPacer
    now, said, spoken = [0.0], [], []
    pacer = AnnouncementPacer(said.append, gap_seconds=45, clock=lambda: now[0])
    pacer('first')
    pacer('second', on_said=lambda: spoken.append(now[0]))
    assert said == ['first'] and spoken == []
    now[0] = 45
    pacer.pump()
    assert said == ['first', 'second'] and spoken == [45]


def held_pacer():
    from assistant.announcement_pacer import AnnouncementPacer
    said, clock, hold = [], Clock(), [True]
    return AnnouncementPacer(said.append, gap_seconds=45, clock=clock, hold=lambda: hold[0]), said, clock, hold


def test_a_hold_keeps_ordinary_notices_and_lets_urgent_ones_through_in_order():
    pacer, said, clock, hold = held_pacer()
    pacer('It may rain today.')
    pacer('The agents need you to review the plan.', urgent=True)
    pacer('Water break?')
    pacer('Reminder: call mom.', urgent=True)
    assert said == ['The agents need you to review the plan.']
    clock.now += 45
    pacer.pump()
    assert said[-1] == 'Reminder: call mom.'
    clock.now += 600
    pacer.pump()
    assert len(said) == 2 and pacer.waiting() == 2


def test_after_the_hold_the_summary_then_the_first_notice_without_a_gap():
    pacer, said, clock, hold = held_pacer()
    pacer('It may rain today.')
    pacer('Water break?')
    clock.now += 100
    hold[0] = False
    pacer.pump()
    assert said == ['While you focused, two notices waited.']
    pacer.pump()
    assert said[-1] == 'It may rain today.'
    clock.now += 45
    pacer.pump()
    assert said[-1] == 'Water break?'
    hold[0] = True
    clock.now += 45
    pacer.pump()
    hold[0] = False
    clock.now += 45
    pacer.pump()
    assert len(said) == 3, 'no summary when nothing waited'


def test_one_waiting_notice_is_named_in_the_singular():
    pacer, said, clock, hold = held_pacer()
    pacer('Water break?')
    hold[0] = False
    pacer.pump()
    assert said == ['While you focused, one notice waited.']


def test_a_summary_not_yet_said_is_dropped_when_the_hold_starts_again():
    pacer, said, clock, hold = held_pacer()
    pacer('Reminder: call mom.', urgent=True)
    pacer('Water break?')
    hold[0] = False
    pacer.pump()  # summary due, but the gap after the reminder has not passed
    hold[0] = True
    clock.now += 45
    pacer.pump()
    assert said == ['Reminder: call mom.']


def test_a_hold_check_that_fails_lets_notices_through():
    from assistant.announcement_pacer import AnnouncementPacer

    def broken():
        raise RuntimeError('no manager yet')
    said = []
    AnnouncementPacer(said.append, clock=Clock(), hold=broken)('Water break?')
    assert said == ['Water break?']


def test_questions_are_counted_and_on_said_still_runs_when_speech_fails():
    from assistant.announcement_pacer import AnnouncementPacer

    def broken(text):
        raise OSError('no audio device')
    opened = []
    pacer = AnnouncementPacer(broken, clock=Clock())
    pacer('Checking in on stretch. Done?', on_said=lambda: opened.append(pacer.question_seq))
    pacer.pump()
    assert opened == [1], 'a coach waiting for its question must not wait forever'


def test_an_urgent_copy_of_a_held_notice_makes_it_urgent():
    pacer, said, clock, hold = held_pacer()
    spoken = []
    pacer('Water break?')
    pacer('Water break?', urgent=True, on_said=lambda: spoken.append(1))
    assert said == ['Water break?'] and pacer.waiting() == 0 and spoken == [1]


class Wall:
    def __init__(self):
        from datetime import datetime
        self.now = datetime(2026, 10, 30, 14, 0)

    def __call__(self):
        return self.now


def doorman_for(present=True):
    from datetime import timedelta  # noqa: F401  (tests move the wall clock)
    from assistant.announcement_pacer import AnnouncementPacer, FocusDoorman
    said, clock, wall, here = [], Clock(), Wall(), [present]
    doorman = None
    pacer = AnnouncementPacer(said.append, gap_seconds=45, clock=clock, hold=lambda: doorman.holding())
    doorman = FocusDoorman(pacer, clock=wall, present=lambda: here[0])
    return pacer, doorman, said, clock, wall, here


def test_nothing_is_held_until_the_owner_starts_a_block():
    pacer, doorman, said, clock, wall, here = doorman_for()
    pacer('It may rain today.')
    assert said == ['It may rain today.']
    assert doorman.intercept("what's waiting") is None


def test_a_block_holds_for_its_minutes_then_lets_the_notices_in():
    from datetime import timedelta
    pacer, doorman, said, clock, wall, here = doorman_for()
    assert doorman.intercept('Vision, do not disturb') == (
        "Okay. I'll hold my notices for 50 minutes. Agents and reminders you timed still come in.")
    pacer('It may rain today.')
    assert said == []
    wall.now += timedelta(minutes=49)
    clock.now += 60
    pacer.pump()
    assert said == []
    wall.now += timedelta(minutes=1)
    pacer.pump()
    assert said == ['While you focused, one notice waited.']


def test_a_block_for_given_minutes_or_hours_and_ended_early():
    from datetime import timedelta
    pacer, doorman, said, clock, wall, here = doorman_for()
    assert doorman.intercept('focus block for 2 hours').startswith("Okay. I'll hold my notices for 2 hours.")
    assert doorman.intercept('hold my notices for 25 minutes').startswith("Okay. I'll hold my notices for 25 minutes.")
    pacer('Water break?')
    wall.now += timedelta(minutes=10)
    assert doorman.intercept('end focus block') == 'Okay, the focus block is over.'
    pacer.pump()
    assert said == ['While you focused, one notice waited.']
    assert doorman.intercept('end focus block') is None


def test_held_notices_wait_for_the_owner_to_come_back():
    from datetime import timedelta
    pacer, doorman, said, clock, wall, here = doorman_for()
    doorman.intercept('do not disturb for 30 minutes')
    pacer('It may rain today.')
    here[0] = False
    wall.now += timedelta(minutes=31)
    pacer.pump()
    assert said == []
    pacer('Stretch?')  # a new notice while away also waits with them
    here[0] = True
    pacer.pump()
    assert said == ['While you focused, two notices waited.']


def test_whats_waiting_and_let_them_through():
    pacer, doorman, said, clock, wall, here = doorman_for()
    doorman.intercept('do not disturb')
    assert doorman.intercept("What's waiting?") == 'Nothing is waiting.'
    pacer('It may rain today.')
    pacer('Water break?')
    assert doorman.intercept('anything waiting') == (
        "Two notices are waiting for the end of your focus block. Say 'let them through' to hear them now.")
    assert doorman.intercept('Let them through.') == 'Okay.'
    assert said == [], 'the reply comes first; the next pump plays them'
    pacer.pump()
    assert said == ['It may rain today.']
    clock.now += 45
    pacer.pump()
    pacer('Stretch?')
    clock.now += 45
    pacer.pump()
    assert said == ['It may rain today.', 'Water break?'], 'the release covered only what was waiting'
    assert doorman.intercept('what is the weather') is None



def test_a_block_length_in_words():
    pacer, doorman, said, clock, wall, here = doorman_for()
    assert doorman.intercept('do not disturb for five minutes').startswith("Okay. I'll hold my notices for 5 minutes.")
    assert doorman.intercept('focus block for forty five minutes').startswith(
        "Okay. I'll hold my notices for 45 minutes.")
    assert doorman.intercept('hold my notices for half an hour').startswith(
        "Okay. I'll hold my notices for 30 minutes.")
