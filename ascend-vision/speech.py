"""Offline, cancellable speech. All engine operations stay on one owning thread."""
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class SpeechOutcome:
    started: bool
    completed: bool
    error: str | None = None


class OfflineSpeaker:
    def __init__(self, config, *, engine_factory=None):
        self.config = config
        self._factory = engine_factory
        self._engine = None
        self._owner = None
        self._counter = 0

    def _get_engine(self):
        owner = threading.get_ident()
        if self._owner is not None and self._owner != owner:
            raise RuntimeError('Speech engine must stay on its owning thread')
        self._owner = owner
        if self._engine is None:
            if self._factory is None:
                import pyttsx3
                self._factory = pyttsx3.init
            self._engine = self._factory()
            self._engine.setProperty('rate', self.config.speech_rate)
            self._engine.setProperty('volume', self.config.speech_volume)
            if self.config.speech_voice:
                voices = self._engine.getProperty('voices')
                if self.config.speech_voice not in {v.id for v in voices}:
                    raise ValueError('Configured speech voice is not installed')
                self._engine.setProperty('voice', self.config.speech_voice)
        return self._engine

    def speak(self, text, cancelled):
        if cancelled():
            return SpeechOutcome(False, False)
        engine = self._get_engine()
        if cancelled():
            return SpeechOutcome(False, False)
        self._counter += 1
        name = f'phone-watch-{self._counter}'
        state = {'started': False, 'done': False, 'completed': False, 'error': False}
        def started(name_received):
            if name_received == name:
                state['started'] = True
        def finished(name_received, completed):
            if name_received == name:
                state.update(done=True, completed=completed)
        def failed(name_received, exception):
            if name_received == name:
                state.update(done=True, error=True)
        # pyttsx3 delivers callback arguments by keyword.
        tokens = [engine.connect('started-utterance', lambda name: started(name)),
                  engine.connect('finished-utterance', lambda name, completed: finished(name, completed)),
                  engine.connect('error', lambda name, exception: failed(name, exception))]
        loop_started = False
        deadline = time.monotonic() + self.config.speech_timeout_seconds
        try:
            engine.say(text, name)
            engine.startLoop(False)
            loop_started = True
            while not state['done']:
                if cancelled():
                    return SpeechOutcome(state['started'], False)
                if time.monotonic() >= deadline:
                    return SpeechOutcome(state['started'], False, 'SpeechTimeout')
                engine.iterate()
                time.sleep(.01)
            if state['error']:
                return SpeechOutcome(state['started'], False, 'SpeechDriverError')
            return SpeechOutcome(state['started'], state['completed'])
        finally:
            try:
                engine.stop()
            finally:
                if loop_started:
                    engine.endLoop()
                for token in tokens:
                    engine.disconnect(token)

    def close(self):
        if self._engine is not None:
            if self._owner != threading.get_ident():
                raise RuntimeError('Speech cleanup must stay on its owning thread')
            self._engine.stop()
            self._engine = None
