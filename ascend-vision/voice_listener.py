"""Real-time microphone voice command listener using SYSTRAN/faster-whisper.

Runs entirely on a dedicated daemon thread with PortAudio InputStream ingestion,
zero disk I/O, energy-based Voice Activity Detection (VAD), and non-blocking callback dispatch.
"""
from dataclasses import dataclass
import logging
import queue
import re
import threading
import time
from typing import Callable

import numpy as np

from config import VoiceCommandConfig

LOG = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK_SIZE = 1600  # 100ms per audio chunk


def compute_rms(audio_chunk: np.ndarray) -> float:
    """Calculate root-mean-square (RMS) energy of a 1D audio waveform."""
    if audio_chunk is None or len(audio_chunk) == 0:
        return 0.0
    audio = audio_chunk.astype(np.float32)
    mean = np.mean(audio)
    return float(np.sqrt(np.mean(np.square(audio - mean))))


class VoiceActivityDetector:
    """Energy-based Voice Activity Detector for segmenting speech from continuous audio."""

    def __init__(
        self,
        energy_threshold: float = 0.02,
        silence_duration_seconds: float = 0.8,
        min_speech_duration_seconds: float = 0.4,
        max_speech_duration_seconds: float = 10.0,
        sample_rate: int = SAMPLE_RATE
    ):
        self.energy_threshold = energy_threshold
        self.silence_duration_seconds = silence_duration_seconds
        self.min_speech_duration_seconds = min_speech_duration_seconds
        self.max_speech_duration_seconds = max_speech_duration_seconds
        self.sample_rate = sample_rate

        self._speech_active = False
        self._accumulated: list[np.ndarray] = []
        self._active_speech_duration = 0.0
        self._silence_duration = 0.0

    @property
    def is_speech_active(self) -> bool:
        return self._speech_active

    def process_chunk(self, chunk: np.ndarray) -> np.ndarray | None:
        """Process incoming 1D float32 audio chunk.

        Returns completed speech utterance buffer if utterance ended, else None.
        """
        if chunk is None or len(chunk) == 0:
            return None

        chunk_duration = len(chunk) / float(self.sample_rate)
        rms = compute_rms(chunk)

        if rms >= self.energy_threshold:
            if not self._speech_active:
                self._speech_active = True
                self._accumulated = [chunk]
                self._active_speech_duration = chunk_duration
                self._silence_duration = 0.0
            else:
                self._accumulated.append(chunk)
                self._active_speech_duration += chunk_duration
                self._silence_duration = 0.0

                if self._active_speech_duration >= self.max_speech_duration_seconds:
                    utterance = np.concatenate(self._accumulated)
                    self.reset()
                    return utterance
        else:
            if self._speech_active:
                self._accumulated.append(chunk)
                self._silence_duration += chunk_duration

                if self._silence_duration >= self.silence_duration_seconds:
                    if self._active_speech_duration >= self.min_speech_duration_seconds:
                        utterance = np.concatenate(self._accumulated)
                        self.reset()
                        return utterance
                    else:
                        self.reset()
                        return None

        return None

    def reset(self):
        self._speech_active = False
        self._accumulated.clear()
        self._active_speech_duration = 0.0
        self._silence_duration = 0.0



@dataclass(frozen=True)
class VoiceCommand:
    action: str  # 'focus', 'pause', 'status', 'mute', 'unmute'
    raw_text: str


class VoiceCommandParser:
    """Matches transcribed text against supported command intents."""

    PATTERNS: list[tuple[str, list[str]]] = [
        ('unmute', [
            r'\bunmute\b', r'\bun-mute\b', r'\bresume audio\b', r'\bresume voice\b',
            r'\bspeak again\b', r'\bunmute voice\b'
        ]),
        ('mute', [
            r'\bmute\b', r'\bquiet\b', r'\bsilence\b', r'\bshut up\b', r'\bstop talking\b',
            r'\bmute audio\b', r'\bmute voice\b'
        ]),
        ('status', [
            r'\bstatus\b', r'\bsummary\b', r'\bstats\b', r'\breport\b',
            r'\bhow am i doing\b', r'\bcheck status\b'
        ]),
        ('focus', [
            r'\bstart focus\b', r'\bfocus mode\b', r'\benable focus\b',
            r'\benter focus\b', r'\bbegin focus\b', r'^focus$'
        ]),
        ('pause', [
            r'\bpause\b', r'\bbreak\b', r'\bstop focus\b', r'\btake a break\b',
            r'\bpause focus\b', r'\bbackground mode\b', r'^stop$'
        ]),
        ('music_toggle', [
            r'\bpause music\b', r'\bplay music\b', r'\btoggle music\b',
            r'\bstop music\b', r'\bresume music\b', r'\bpause spotify\b',
            r'\bplay spotify\b', r'\bspotify\b'
        ]),
    ]

    def parse(self, text: str) -> VoiceCommand | None:
        if not text or not text.strip():
            return None

        normalized = text.lower().strip()
        normalized = re.sub(r'^[^\w]+|[^\w]+$', '', normalized)

        for action, regex_list in self.PATTERNS:
            for pattern in regex_list:
                if re.search(pattern, normalized):
                    return VoiceCommand(action=action, raw_text=text)

        return None


class VoiceCommandListener:
    """Background listener capturing microphone audio and recognizing commands."""

    def __init__(
        self,
        config: VoiceCommandConfig,
        callback: Callable[[VoiceCommand], None] | None = None,
        unmatched_callback: Callable[[str], None] | None = None,
        is_speaking: Callable[[], bool] | None = None,
        *,
        model=None,
        stream_factory=None
    ):
        self.config = config
        self.callback = callback
        self.unmatched_callback = unmatched_callback
        self.is_speaking = is_speaking
        self._model = model
        self._stream_factory = stream_factory
        self._last_chat_time: float | None = None

        self.vad = VoiceActivityDetector(
            energy_threshold=config.energy_threshold,
            silence_duration_seconds=config.silence_duration_seconds,
            min_speech_duration_seconds=config.min_speech_duration_seconds,
            max_speech_duration_seconds=config.max_speech_duration_seconds,
            sample_rate=SAMPLE_RATE
        )
        self.parser = VoiceCommandParser()

        self._audio_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self.enabled = config.enabled
        self._is_recording = False
        self._is_transcribing = False
        self._last_heard_text: str = ""
        self._last_heard_time: float = 0.0
        self._current_rms: float = 0.0
        from collections import deque
        self._waveform_history = deque(maxlen=40)

    @property
    def is_recording(self) -> bool:
        """True if user is actively speaking into the microphone."""
        return self._is_recording or self.vad.is_speech_active

    @property
    def is_transcribing(self) -> bool:
        """True if Whisper is currently transcribing an utterance."""
        return self._is_transcribing

    @property
    def current_rms(self) -> float:
        """Most recent RMS audio amplitude."""
        return self._current_rms

    @property
    def waveform(self) -> list[float]:
        """Recent normalized amplitude history for dynamic equalizer/waveform visualization."""
        return list(self._waveform_history)

    @property
    def last_heard(self) -> tuple[str, float]:
        """Returns tuple of (last_heard_text, timestamp_seconds)."""
        return self._last_heard_text, self._last_heard_time

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            LOG.debug('Audio InputStream status: %s', status)
        flat = indata.copy().flatten()
        rms = compute_rms(flat)
        self._current_rms = rms
        self._waveform_history.append(rms)
        if self.is_speaking is not None and self.is_speaking():
            # Mute microphone audio while TTS is actively playing to prevent self-triggering
            self.vad.reset()
            return
        try:
            self._audio_queue.put_nowait(flat)
        except queue.Full:
            LOG.warning('Audio buffer queue full; dropping frame')

    def start(self):
        if not self.config.enabled:
            return
        if self._thread is not None or self._stop.is_set():
            raise RuntimeError('VoiceCommandListener is single-use')

        if self._stream is None:
            if self._stream_factory is not None:
                self._stream = self._stream_factory(self._audio_callback)
            else:
                try:
                    import sounddevice as sd
                    dev_idx = getattr(self.config, 'device_index', None)
                    self._stream = sd.InputStream(
                        device=dev_idx,
                        samplerate=SAMPLE_RATE,
                        channels=1,
                        dtype='float32',
                        blocksize=BLOCK_SIZE,
                        callback=self._audio_callback
                    )
                    self._stream.start()
                    dev_name = dev_idx if dev_idx is not None else 'default'
                    LOG.info('Microphone audio stream started at %d Hz (device=%s)', SAMPLE_RATE, dev_name)
                except Exception as exc:
                    LOG.warning('Microphone unavailable for voice commands: %s', exc)
                    self.enabled = False
                    return

        self._thread = threading.Thread(target=self._run, name='voice-listener', daemon=True)
        self._thread.start()
        LOG.info('Voice command background listener started')

    def _init_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            LOG.info(
                'Loading Faster-Whisper (%s, %s, %s, threads=%d)...',
                self.config.model_size, self.config.device, self.config.compute_type, self.config.cpu_threads
            )
            self._model = WhisperModel(
                self.config.model_size,
                device=self.config.device,
                compute_type=self.config.compute_type,
                cpu_threads=self.config.cpu_threads
            )
            LOG.info('Faster-Whisper model ready')

    def _run(self):
        try:
            self._init_model()
        except Exception as exc:
            LOG.error('Failed to initialize Whisper model: %s', exc)
            return

        while not self._stop.is_set():
            if self.is_speaking is not None and self.is_speaking():
                self.vad.reset()
                while not self._audio_queue.empty():
                    try:
                        self._audio_queue.get_nowait()
                    except queue.Empty:
                        break
                time.sleep(0.05)
                continue

            try:
                chunk = self._audio_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            try:
                utterance = self.vad.process_chunk(chunk)
                if utterance is not None and not self._stop.is_set():
                    self._is_transcribing = True
                    try:
                        segments, _ = self._model.transcribe(
                            utterance,
                            language='en',
                            beam_size=1
                        )
                        text = ' '.join(seg.text.strip() for seg in segments).strip()
                    finally:
                        self._is_transcribing = False

                    if text:
                        self._last_heard_text = text
                        self._last_heard_time = time.monotonic()
                        LOG.info('HEARD_SPEECH: "%s"', text)
                        cmd = self.parser.parse(text)
                        if cmd is not None:
                            LOG.info('VOICE_COMMAND_DETECTED: action=%s text="%s"', cmd.action, cmd.raw_text)
                            if self.callback is not None:
                                try:
                                    self.callback(cmd)
                                except Exception as err:
                                    LOG.error('Voice command callback failed: %s', err)
                        elif getattr(self.config, 'conversational_mode', False) and self.unmatched_callback is not None:
                            now = time.monotonic()
                            cooldown = getattr(self.config, 'chat_cooldown_seconds', 5.0)
                            if self._last_chat_time is not None and (now - self._last_chat_time) < cooldown:
                                LOG.debug('Chat cooldown active (%.1fs remaining); ignoring: "%s"',
                                          cooldown - (now - self._last_chat_time), text)
                            else:
                                self._last_chat_time = now
                                LOG.info('ROUTING_TO_CONVERSATION: "%s"', text)
                                try:
                                    self.unmatched_callback(text)
                                except Exception as err:
                                    LOG.error('Conversational handler callback failed: %s', err)
            except Exception as exc:
                LOG.warning('Error in voice transcription loop: %s', exc)

    def close(self):
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)

