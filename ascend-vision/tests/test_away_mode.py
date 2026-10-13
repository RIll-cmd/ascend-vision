import pytest

from assistant.away_mode import (ACK_HERE, ACK_REST, ASK, ASLEEP_REPLY, SLEEP_SILENT, WAKE_BY_FACE,
                                 WAKE_BY_VOICE, AwayMode)


class Harness:
    def __init__(self, focus=True):
        self.now = 0.0
        self.focus = focus
        self.said = []
        self.calls = []
        self.away = AwayMode(
            is_focus=lambda: self.focus,
            pause=lambda: (self.calls.append('pause'), setattr(self, 'focus', False)),
            resume=lambda: (self.calls.append('resume'), setattr(self, 'focus', True)),
            announce=self.said.append, away_after=300, answer_window=120, wake_after=3,
            clock=lambda: self.now)

    def frames(self, seconds, face, step=1.0):
        for _ in range(int(seconds / step)):
            self.now += step
            self.away.observe(face)


def test_asks_once_after_five_minutes_away_and_resets_when_the_face_returns():
    h = Harness()
    h.frames(299, face=False)
    assert h.said == []
    h.frames(2, face=False)
    assert h.said == [ASK] and h.away.state == 'asking'
    h.frames(5, face=True)
    assert h.away.state == 'active' and h.said == [ASK]


def test_no_answer_puts_vision_to_sleep_and_sitting_down_wakes_it():
    h = Harness()
    h.frames(301, face=False)
    h.frames(121, face=False)
    assert h.away.state == 'sleeping' and h.said[-1] == SLEEP_SILENT and h.calls == ['pause']
    h.frames(2, face=True)
    assert h.away.state == 'sleeping'          # a glance is not enough
    h.frames(2, face=True)
    assert h.away.state == 'active' and h.said[-1] == WAKE_BY_FACE and h.calls == ['pause', 'resume']


@pytest.mark.parametrize('answer', ["yes, I'm just resting", 'taking a break', 'brb, eating lunch'])
def test_resting_answer_sleeps_with_the_promised_reply(answer):
    h = Harness()
    h.frames(301, face=False)
    assert h.away.intercept(answer) == ACK_REST
    assert h.away.state == 'sleeping' and h.calls == ['pause']


def test_while_asleep_only_wake_up_gets_through_and_no_ai_is_used():
    h = Harness()
    h.frames(301, face=False)
    h.away.intercept('resting')
    assert h.away.intercept('what is the weather?') == ASLEEP_REPLY
    assert h.away.intercept('wake up') == WAKE_BY_VOICE
    assert h.away.state == 'active' and h.calls == ['pause', 'resume']
    assert h.away.intercept('what is the weather?') is None    # awake: normal AI chat again


def test_yes_keeps_working_and_a_real_question_goes_to_the_ai():
    h = Harness()
    h.frames(301, face=False)
    assert h.away.intercept("yes I'm here") == ACK_HERE and h.away.state == 'active'
    h.frames(301, face=False)
    assert h.away.intercept('how many commits did I make today?') is None
    assert h.away.state == 'active'


def test_never_asks_when_the_owner_paused_by_hand_and_manual_resume_ends_sleep():
    h = Harness(focus=False)
    h.frames(900, face=False)
    assert h.said == [] and h.away.state == 'active'
    h.focus = True
    h.frames(301, face=False)
    h.away.intercept('resting')
    h.frames(2, face=False)
    assert h.away.state == 'sleeping'            # our own pause is still taking effect
    h.focus = True                               # owner pressed Resume in the window
    h.frames(6, face=False)
    assert h.away.state == 'active'


def test_go_to_sleep_command_works_any_time():
    h = Harness()
    assert h.away.intercept('you can rest now') == ACK_REST
    assert h.away.state == 'sleeping'


def test_conversation_answers_locally_without_calling_the_ai():
    from assistant.local_conversation import LocalConversation

    class Queue:
        def __init__(self):
            self.events = []
        def begin_session(self, _id): pass
        def publish_event(self, _session, **event): self.events.append(event)

    class Assistant:
        called = False
        def respond(self, *args, **kwargs):
            Assistant.called = True
            raise AssertionError('the AI must not be called')

    queue = Queue()
    conversation = LocalConversation(queue, Assistant(), lambda _text: None, session_key=('local', 'voice', '1'),
                                     interceptor=lambda text: ASLEEP_REPLY)
    conversation.start()
    assert conversation.handle_message({'text': 'hello', 'source': 'voice', 'message_id': 'm1'}) == ASLEEP_REPLY
    assert not Assistant.called
    assert queue.events[-1]['kind'] == 'assistant' and queue.events[-1]['reply_source'] == 'tool'


def test_settings_validate(tmp_path):
    from config import load_config
    path = tmp_path / 'config.yaml'
    path.write_text('away_mode:\n  away_after_seconds: 600\n', encoding='utf-8')
    assert load_config(path).away_mode.away_after_seconds == 600
    path.write_text('away_mode:\n  away_after_seconds: 5\n', encoding='utf-8')
    with pytest.raises(ValueError):
        load_config(path)
