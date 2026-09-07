"""Unit tests for the Kokoro-ONNX TTS engine integration."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest

from config import FeedbackConfig
from speech import SpeechOutcome
from tts_engine import KokoroSpeaker, ensure_kokoro_assets


def test_ensure_kokoro_assets_downloads_missing_files(tmp_path):
    fake_data = b"0" * (2 * 1024 * 1024)

    class FakeResponse:
        def __init__(self):
            self.data = fake_data
            self.pos = 0

        def read(self, chunk_size):
            if self.pos >= len(self.data):
                return b""
            chunk = self.data[self.pos:self.pos + chunk_size]
            self.pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    with patch('shutil.which', return_value=None), patch('urllib.request.urlopen', side_effect=lambda *args, **kwargs: FakeResponse()) as mock_urlopen:
        model_path = tmp_path / 'kokoro.onnx'
        voices_path = tmp_path / 'voices.bin'

        out_model, out_voices = ensure_kokoro_assets(model_path, voices_path)
        assert out_model == model_path.resolve()
        assert out_voices == voices_path.resolve()
        assert model_path.is_file()
        assert voices_path.is_file()
        assert mock_urlopen.call_count == 2

        # Second invocation should not download since files exist with valid size
        ensure_kokoro_assets(model_path, voices_path)
        assert mock_urlopen.call_count == 2


def test_speaker_cancelled_before_inference_does_not_initialize():
    factory = MagicMock()
    config = FeedbackConfig(tts_engine='kokoro_onnx')
    speaker = KokoroSpeaker(config, kokoro_factory=factory)
    result = speaker.speak('Hello there', cancelled=lambda: True)
    assert result == SpeechOutcome(False, False)
    factory.assert_not_called()
    assert speaker.is_speaking is False


def test_speaker_mock_inference_and_playback():
    mock_kokoro = MagicMock()
    # 24kHz audio, 0.1s of audio samples
    mock_audio = np.zeros(2400, dtype=np.float32)
    mock_kokoro.create.return_value = (mock_audio, 24000)

    config = FeedbackConfig(
        tts_engine='kokoro_onnx',
        tts_voice='af_heart',
        tts_speed=1.05,
        speech_volume=0.8
    )
    speaker = KokoroSpeaker(config, kokoro_factory=lambda: mock_kokoro)

    with patch('sounddevice.OutputStream') as mock_stream_cls:
        mock_stream = MagicMock()
        mock_stream_cls.return_value.__enter__.return_value = mock_stream

        result = speaker.speak('Testing Kokoro ONNX', cancelled=lambda: False)

        assert result.started is True
        assert result.completed is True
        assert result.error is None
        mock_kokoro.create.assert_called_once_with(
            text='Testing Kokoro ONNX',
            voice='af_heart',
            speed=1.05,
            lang='en-us'
        )
        assert mock_stream.write.called
        assert speaker.is_speaking is False


def test_speaker_direct_instantiation_arguments(tmp_path):
    mock_kokoro = MagicMock()
    mock_audio = np.zeros(2400, dtype=np.float32)
    mock_kokoro.create.return_value = (mock_audio, 24000)

    speaker = KokoroSpeaker(
        model_path=str(tmp_path / 'custom_kokoro.onnx'),
        voices_path=str(tmp_path / 'custom_voices.bin'),
        default_voice='af_bella',
        default_speed=1.10,
        kokoro_factory=lambda: mock_kokoro
    )

    assert speaker.voice == 'af_bella'
    assert speaker.speed == 1.10

    with patch('sounddevice.OutputStream') as mock_stream_cls:
        mock_stream = MagicMock()
        mock_stream_cls.return_value.__enter__.return_value = mock_stream

        result = speaker.speak('Direct argument test')
        assert result.completed is True
        mock_kokoro.create.assert_called_once_with(
            text='Direct argument test',
            voice='af_bella',
            speed=1.10,
            lang='en-us'
        )
        assert speaker.is_speaking is False


def test_speaker_is_speaking_flag_during_playback():
    mock_kokoro = MagicMock()
    mock_audio = np.zeros(4800, dtype=np.float32)
    mock_kokoro.create.return_value = (mock_audio, 24000)

    speaker = KokoroSpeaker(kokoro_factory=lambda: mock_kokoro)
    speaking_state_during_write = []

    with patch('sounddevice.OutputStream') as mock_stream_cls:
        mock_stream = MagicMock()

        def on_write(chunk):
            speaking_state_during_write.append(speaker.is_speaking)

        mock_stream.write.side_effect = on_write
        mock_stream_cls.return_value.__enter__.return_value = mock_stream

        speaker.speak('Testing speaking flag')

        assert len(speaking_state_during_write) > 0
        assert all(state is True for state in speaking_state_during_write)
        assert speaker.is_speaking is False


def test_speaker_compute_blend():
    mock_kokoro = MagicMock()
    v1 = np.ones((510, 1, 256), dtype=np.float32)
    v2 = np.full((510, 1, 256), 2.0, dtype=np.float32)
    mock_kokoro.get_voice_style.side_effect = lambda name: v1 if name == 'jf_tebukuro' else v2

    blend_specs = [
        {'name': 'jf_tebukuro', 'weight': 0.65},
        {'name': 'af_bella', 'weight': 0.35}
    ]

    speaker = KokoroSpeaker(
        voice_blend=blend_specs,
        speed=1.10,
        lang='en-us',
        kokoro_factory=lambda: mock_kokoro
    )

    assert speaker.voice_style is not None
    assert speaker.voice_style.shape == (510, 1, 256)
    expected = (v1 * 0.65) + (v2 * 0.35)
    np.testing.assert_allclose(speaker.voice_style, expected, rtol=1e-5)


def test_speaker_synthesize_with_blended_voice():
    mock_kokoro = MagicMock()
    v1 = np.ones((510, 1, 256), dtype=np.float32)
    mock_kokoro.get_voice_style.return_value = v1
    mock_audio = np.zeros(2400, dtype=np.float32)
    mock_kokoro.create.return_value = (mock_audio, 24000)

    config = FeedbackConfig(
        tts_engine='kokoro_onnx',
        tts_speed=1.10,
        tts_lang='en-us',
        tts_voice_blend=[
            {'name': 'jf_tebukuro', 'weight': 0.65},
            {'name': 'af_bella', 'weight': 0.35}
        ]
    )
    speaker = KokoroSpeaker(config, kokoro_factory=lambda: mock_kokoro)

    with patch('sounddevice.OutputStream') as mock_stream_cls:
        mock_stream = MagicMock()
        mock_stream_cls.return_value.__enter__.return_value = mock_stream

        result = speaker.speak('Test with blended voice')
        assert result.completed is True
        # Verify kokoro.create was called with the precomputed voice_style array
        mock_kokoro.create.assert_called_once()
        call_kwargs = mock_kokoro.create.call_args[1]
        assert call_kwargs['text'] == 'Test with blended voice'
        assert call_kwargs['speed'] == 1.10
        assert call_kwargs['lang'] == 'en-us'
        assert isinstance(call_kwargs['voice'], np.ndarray)
        assert call_kwargs['voice'].shape == (510, 1, 256)

