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
