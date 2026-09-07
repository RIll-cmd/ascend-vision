"""Local Kokoro-ONNX TTS speaker for Phone Watch.

Uses the lightweight Kokoro-ONNX model (kokoro-v1.0.onnx) and voices-v1.0.bin
to perform local offline text-to-speech inference and playback with sounddevice.
"""
from dataclasses import dataclass
import logging
from pathlib import Path
import tempfile
import threading
import time
from typing import Callable, Optional
import urllib.request

import numpy as np

from speech import SpeechOutcome

LOG = logging.getLogger(__name__)

KOKORO_MODEL_URL = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx'
KOKORO_VOICES_URL = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin'


def _download_file(url: str, dest_path: Path):
    dest_path = Path(dest_path).resolve()
    if dest_path.is_file() and dest_path.stat().st_size > 1024 * 1024:
        return dest_path
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = dest_path.with_suffix('.part')
    LOG.info('Downloading Kokoro asset from %s to %s...', url, dest_path)
    downloaded = False
    import shutil
    import subprocess
    curl_bin = shutil.which('curl.exe') or shutil.which('curl')
    if curl_bin:
        try:
            subprocess.run([curl_bin, '-L', url, '-o', str(temp_path)], check=True, timeout=300)
            if temp_path.is_file() and temp_path.stat().st_size >= 1024:
                temp_path.replace(dest_path)
                downloaded = True
                LOG.info('Successfully downloaded %s via curl (size=%.1fMB)', dest_path.name, dest_path.stat().st_size / (1024 * 1024))
        except Exception as curl_err:
            LOG.warning('curl download failed (%s), falling back to urllib', curl_err)

    if not downloaded:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'phone_watch/1.0'})
            with urllib.request.urlopen(req, timeout=180) as response:
                with tempfile.NamedTemporaryFile(dir=dest_path.parent, suffix='.part', delete=False) as out:
                    temp_path = Path(out.name)
                    while chunk := response.read(1024 * 1024):
                        out.write(chunk)
            if temp_path.stat().st_size < 1024:
                raise RuntimeError(f'Downloaded asset {dest_path.name} is unexpectedly small')
            temp_path.replace(dest_path)
            LOG.info('Successfully downloaded %s (size=%.1fMB)', dest_path.name, dest_path.stat().st_size / (1024 * 1024))
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)
    return dest_path


def ensure_kokoro_assets(
    model_path: Path = Path('models/kokoro/kokoro-v1.0.onnx'),
    voices_path: Path = Path('models/kokoro/voices-v1.0.bin')
) -> tuple[Path, Path]:
    """Ensure Kokoro ONNX model and voices binary exist locally, downloading if absent."""
    model_path = Path(model_path).resolve()
    voices_path = Path(voices_path).resolve()
    if not model_path.is_file() or model_path.stat().st_size < 1024 * 1024:
        _download_file(KOKORO_MODEL_URL, model_path)
    if not voices_path.is_file() or voices_path.stat().st_size < 1024:
        _download_file(KOKORO_VOICES_URL, voices_path)
    return model_path, voices_path


class KokoroSpeaker:
    """Offline TTS Speaker using Kokoro-ONNX architecture with voice style blending."""

    def __init__(
        self,
        config_or_model_path=None,
        voices_path: Optional[str | Path] = None,
        default_voice: Optional[str] = None,
        default_speed: Optional[float] = None,
        *,
        model_path: Optional[str | Path] = None,
        voice_blend: Optional[list | tuple] = None,
        speed: Optional[float] = None,
        lang: Optional[str] = None,
        kokoro_factory: Optional[Callable] = None,
        **kwargs
    ):
        if isinstance(default_voice, (list, tuple)):
            resolved_voice_blend = default_voice
            resolved_voice = 'af_heart'
        else:
            resolved_voice_blend = voice_blend
            resolved_voice = default_voice

        if hasattr(config_or_model_path, 'tts_model_path'):
            self.config = config_or_model_path
            resolved_model = model_path or getattr(self.config, 'tts_model_path', 'models/kokoro/kokoro-v1.0.onnx')
            resolved_voices = voices_path or getattr(self.config, 'tts_voices_path', 'models/kokoro/voices-v1.0.bin')
            resolved_voice = resolved_voice or getattr(self.config, 'tts_voice', 'af_heart')
            resolved_speed = speed if speed is not None else (default_speed if default_speed is not None else getattr(self.config, 'tts_speed', 1.05))
            resolved_lang = lang or getattr(self.config, 'tts_lang', 'en-us')
            if resolved_voice_blend is None:
                resolved_voice_blend = getattr(self.config, 'tts_voice_blend', None)
        else:
            self.config = None
            resolved_model = model_path or config_or_model_path or 'models/kokoro/kokoro-v1.0.onnx'
            resolved_voices = voices_path or 'models/kokoro/voices-v1.0.bin'
            resolved_voice = resolved_voice or 'af_heart'
            resolved_speed = speed if speed is not None else (default_speed if default_speed is not None else 1.05)
            resolved_lang = lang or 'en-us'

        self.model_path = Path(resolved_model).resolve()
        self.voices_path = Path(resolved_voices).resolve()
        self.voice = str(resolved_voice)
        self.speed = float(resolved_speed)
        self.lang = str(resolved_lang)
        self.voice_blend = resolved_voice_blend
        self._kokoro_factory = kokoro_factory
        self._kokoro = None
        self._lock = threading.Lock()
        self._is_speaking = False

        # Precompute the composite voice style vector at initialization
        self.voice_style = self._compute_blend(self.voice_blend)

    @property
    def kokoro(self):
        return self._init_kokoro()

    @property
    def is_speaking(self) -> bool:
        """True if speech audio is currently playing."""
        return self._is_speaking

    @is_speaking.setter
    def is_speaking(self, value: bool):
        self._is_speaking = bool(value)

    def _init_kokoro(self):
        with self._lock:
            if self._kokoro is None:
                if self._kokoro_factory is not None:
                    self._kokoro = self._kokoro_factory()
                    return self._kokoro
                ensure_kokoro_assets(self.model_path, self.voices_path)
                from kokoro_onnx import Kokoro
                LOG.info('Initializing Kokoro-ONNX with model=%s, voices=%s...', self.model_path.name, self.voices_path.name)
                self._kokoro = Kokoro(str(self.model_path), str(self.voices_path))
                LOG.info('Kokoro-ONNX initialized successfully.')
            return self._kokoro

    def _compute_blend(self, blend_specs: Optional[list | tuple]):
        """Precompute composite voice style vector from multiple weighted voices."""
        if not blend_specs:
            return None
        kokoro = self._init_kokoro()
        composite = None
        for spec in blend_specs:
            name = spec["name"] if isinstance(spec, dict) or hasattr(spec, "__getitem__") else getattr(spec, "name")
            weight = float(spec["weight"] if isinstance(spec, dict) or hasattr(spec, "__getitem__") else getattr(spec, "weight"))
            style = kokoro.get_voice_style(name)
            weighted = style * weight
            composite = weighted if composite is None else (composite + weighted)
        return composite

    def warmup(self):
        """Pre-warms Kokoro-ONNX inference with a 1-word sample."""
        try:
            kokoro = self._init_kokoro()
            t0 = time.perf_counter()
            target_voice = self.voice_style if self.voice_style is not None else self.voice
            kokoro.create("Warmup.", voice=target_voice, speed=self.speed, lang=self.lang)
            voice_label = 'blended' if self.voice_style is not None else self.voice
            LOG.info('Kokoro-ONNX pre-warmed in %.2fs (voice=%s, speed=%.2f, lang=%s)',
                     time.perf_counter() - t0, voice_label, self.speed, self.lang)
        except Exception as exc:
            LOG.warning('Kokoro-ONNX pre-warmup skipped/failed: %s', exc)

    def speak(self, text: str, cancelled: Optional[Callable[[], bool]] = None) -> SpeechOutcome:
        """Infers audio via Kokoro-ONNX and plays via sounddevice with cancellation support."""
        if cancelled is None:
            cancelled = lambda: False
        if cancelled():
            return SpeechOutcome(False, False)

        try:
            kokoro = self._init_kokoro()
        except Exception as exc:
            LOG.error('Failed to initialize Kokoro-ONNX engine: %s', exc)
            return SpeechOutcome(False, False, error=type(exc).__name__)

        if cancelled():
            return SpeechOutcome(False, False)

        target_voice = self.voice_style if self.voice_style is not None else self.voice
        voice_label = 'blended' if self.voice_style is not None else self.voice
        LOG.info('Generating Kokoro audio for: "%s" (voice=%s, speed=%.2f, lang=%s)',
                 text, voice_label, self.speed, self.lang)
        before_infer = time.perf_counter()
        try:
            samples, sample_rate = kokoro.create(
                text=text,
                voice=target_voice,
                speed=self.speed,
                lang=self.lang
            )
        except Exception as exc:
            LOG.error('Kokoro inference failed: %s', exc)
            return SpeechOutcome(False, False, error=type(exc).__name__)

        infer_dur = time.perf_counter() - before_infer
        LOG.info('Kokoro inference completed in %.2fs (sr=%d, samples=%d)', infer_dur, sample_rate, len(samples))

        if cancelled():
            return SpeechOutcome(False, False)

        # Apply configured speech_volume
        volume = getattr(self.config, 'speech_volume', 1.0) if self.config else 1.0
        audio_data = (samples * volume).astype(np.float32)

        import sounddevice as sd

        timeout = getattr(self.config, 'speech_timeout_seconds', 20.0) if self.config else 20.0
        deadline = time.monotonic() + timeout

        # Stream playback in small blocks to allow responsive cancellation
        block_size = int(sample_rate * 0.05)  # 50ms chunks
        total_samples = len(audio_data)
        current_pos = 0
        started = False

        self._is_speaking = True
        try:
            with sd.OutputStream(samplerate=sample_rate, channels=1, dtype='float32') as stream:
                started = True
                while current_pos < total_samples:
                    if cancelled():
                        return SpeechOutcome(started, False)
                    if time.monotonic() >= deadline:
                        return SpeechOutcome(started, False, error='SpeechTimeout')

                    chunk = audio_data[current_pos:current_pos + block_size]
                    if len(chunk) < block_size:
                        chunk = np.pad(chunk, (0, block_size - len(chunk)))
                    stream.write(chunk)
                    current_pos += block_size

            return SpeechOutcome(started, True)
        except Exception as exc:
            LOG.error('Audio playback failed: %s', exc)
            return SpeechOutcome(started, False, error=type(exc).__name__)
        finally:
            self._is_speaking = False

    def close(self):
        """Clean up loaded TTS resources."""
        self._kokoro = None
        self._is_speaking = False


# Backward-compatible alias
StyleBertVits2Speaker = KokoroSpeaker
ensure_model_assets = ensure_kokoro_assets
