"""Gesture mute must interrupt active and queued speech."""
import threading
import time

from config import FeedbackConfig
from feedback import FeedbackService
from speech import SpeechOutcome


class _BlockingSpeaker:
    def __init__(self):
        self.started = threading.Event()
        self.cancelled = threading.Event()

    def speak(self, _text, is_cancelled):
        self.started.set()
        while not is_cancelled():
            time.sleep(0.005)
        self.cancelled.set()
        return SpeechOutcome(started=True, completed=False)

    def close(self):
        pass


def test_cancel_speech_interrupts_active_tts():
    speaker = _BlockingSpeaker()
    service = FeedbackService(FeedbackConfig(enabled=True), cooldown_seconds=0.0, speaker=speaker)
    service.start()
    try:
        assert service.speak_announcement("This must stop.") is True
        assert speaker.started.wait(1.0)
        service.cancel_speech()
        assert speaker.cancelled.wait(1.0)
    finally:
        service.close()
