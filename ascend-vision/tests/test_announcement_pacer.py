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


def focused_pacer():
    from assistant.announcement_pacer import AnnouncementPacer, FocusDoorman
    said, clock, focus = [], Clock(), [True]
    pacer = AnnouncementPacer(said.append, gap_seconds=45, clock=clock, hold=lambda: focus[0])
    return pacer, FocusDoorman(pacer), said, clock, focus


def test_focus_holds_ordinary_notices_and_lets_urgent_ones_through_in_order():
    pacer, _, said, clock, focus = focused_pacer()
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


def test_after_focus_one_summary_then_the_waiting_notices_in_order():
    pacer, _, said, clock, focus = focused_pacer()
    pacer('It may rain today.')
    pacer('Water break?')
    clock.now += 100
    focus[0] = False
    pacer.pump()
    assert said == ['While you focused, two notices waited.']
    for _ in range(2):
        clock.now += 45
        pacer.pump()
    assert said[1:] == ['It may rain today.', 'Water break?']
    focus[0] = True
    clock.now += 45
    pacer.pump()
    focus[0] = False
    clock.now += 45
    pacer.pump()
    assert len(said) == 3, 'no summary when nothing waited'


def test_one_waiting_notice_is_named_in_the_singular():
    pacer, _, said, clock, focus = focused_pacer()
    pacer('Water break?')
    focus[0] = False
    pacer.pump()
    assert said == ['While you focused, one notice waited.']


def test_whats_waiting_and_let_them_through():
    pacer, doorman, said, clock, focus = focused_pacer()
    assert doorman.intercept("What's waiting?") == 'Nothing is waiting.'
    pacer('It may rain today.')
    pacer('Water break?')
    assert doorman.intercept('anything waiting') == (
        "Two notices are waiting for the end of your focus block. Say 'let them through' to hear them now.")
    assert doorman.intercept('Let them through.') == 'Okay.'
    assert said == ['It may rain today.']
    clock.now += 45
    pacer.pump()
    assert said[-1] == 'Water break?'
    pacer('Stretch?')
    clock.now += 45
    pacer.pump()
    assert said[-1] == 'Water break?', 'the release covered only what was waiting'
    assert doorman.intercept('what is the weather') is None


def test_a_hold_check_that_fails_lets_notices_through():
    from assistant.announcement_pacer import AnnouncementPacer

    def broken():
        raise RuntimeError('no manager yet')
    said = []
    AnnouncementPacer(said.append, clock=Clock(), hold=broken)('Water break?')
    assert said == ['Water break?']


def test_dedupe_spans_urgent_and_held_notices():
    pacer, _, said, clock, focus = focused_pacer()
    pacer('Water break?')
    pacer('Water break?', urgent=True)
    assert said == [] and pacer.waiting() == 1
