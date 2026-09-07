import threading

import pytest

from config import FeedbackConfig
from speech import OfflineSpeaker, SpeechOutcome


class Engine:
    def __init__(self):
        self.handlers = {}
        self.step = 0
        self.stopped = False
        self.ended = False
    def setProperty(self, *args): pass
    def connect(self, event, callback):
        self.handlers[event] = callback
        return event
    def disconnect(self, event): self.handlers.pop(event)
    def say(self, text, name): self.name = name
    def startLoop(self, use_driver): assert use_driver is False
    def iterate(self):
        self.step += 1
        if self.step == 1:
            self.handlers['started-utterance'](name=self.name)
        else:
            self.handlers['finished-utterance'](name=self.name, completed=True)
    def stop(self): self.stopped = True
    def endLoop(self): self.ended = True


def test_playback_completes_and_cleans_up_callbacks():
    engine = Engine()
    speaker = OfflineSpeaker(FeedbackConfig(), engine_factory=lambda: engine)
    assert speaker.speak('Focus again.', lambda: False) == SpeechOutcome(True, True)
    assert engine.stopped and engine.ended and not engine.handlers
    speaker.close()


def test_cancel_during_speech_records_that_it_started():
    engine = Engine()
    speaker = OfflineSpeaker(FeedbackConfig(), engine_factory=lambda: engine)
    assert speaker.speak('Focus again.', lambda: engine.step >= 1) == SpeechOutcome(True, False)
    assert engine.stopped and engine.ended
    speaker.close()


def test_cancel_before_speech_does_not_initialize_engine():
    def fail(): pytest.fail('Cancelled job must not create a speech engine')
    speaker = OfflineSpeaker(FeedbackConfig(), engine_factory=fail)
    assert speaker.speak('No speech.', lambda: True) == SpeechOutcome(False, False)


def test_timeout_stops_driver():
    engine = Engine()
    engine.iterate = lambda: None
    speaker = OfflineSpeaker(FeedbackConfig(speech_timeout_seconds=.1), engine_factory=lambda: engine)
    result = speaker.speak('No completion callback.', lambda: False)
    assert result.error == 'SpeechTimeout'
    assert engine.stopped and engine.ended
    speaker.close()
