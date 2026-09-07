from pathlib import Path

import pytest

from config import AscendConfig, FeedbackConfig, VoiceBlendItem, load_config


def test_defaults_and_relative_model_path(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('detector:\n  model: models/yolo26n.pt\n')
    cfg = load_config(path)
    assert cfg.camera.width == 640
    assert cfg.detector.confidence == 0.5
    assert cfg.detector.model == tmp_path / 'models/yolo26n.pt'
    assert cfg.hold.threshold_frames == 12


@pytest.mark.parametrize('content', [
    'detector:\n  confidence: 1.5', 'camera:\n  fps: 0',
    'camera:\n  width: true', 'detector:\n  confidence: .nan',
    'detector:\n  confdence: 0.4', 'preview: yes',
    'camera:\n  index: -1', 'hold:\n  cooldown_seconds: -1',
    'runtime:\n  preview: "false"', '[]',
])
def test_invalid_settings_fail_early(tmp_path, content):
    path = tmp_path / 'config.yaml'
    path.write_text(content)
    with pytest.raises(ValueError):
        load_config(path)


def test_missing_config_is_not_silently_ignored(tmp_path):
    with pytest.raises(ValueError, match='config'):
        load_config(tmp_path / 'absent.yaml')


def test_feedback_config_kokoro_validation():
    from config import FeedbackConfig

    # Valid config
    cfg = FeedbackConfig(
        tts_engine='kokoro_onnx',
        tts_model_path=Path('models/kokoro/kokoro-v1.0.onnx'),
        tts_voices_path=Path('models/kokoro/voices-v1.0.bin'),
        tts_voice='af_bella',
        tts_speed=1.15
    )
    assert cfg.tts_engine == 'kokoro_onnx'
    assert cfg.tts_voice == 'af_bella'
    assert cfg.tts_speed == 1.15

    # Invalid engine
    with pytest.raises(ValueError, match='feedback.tts_engine'):
        FeedbackConfig(tts_engine='invalid_engine')

    # Invalid speed
    with pytest.raises(ValueError, match='feedback.tts_speed'):
        FeedbackConfig(tts_speed=0.05)


def test_feedback_config_voice_blend():
    # Valid voice blend
    cfg = FeedbackConfig(
        tts_lang='en-us',
        tts_voice_blend=[
            {'name': 'jf_tebukuro', 'weight': 0.65},
            {'name': 'af_bella', 'weight': 0.35}
        ]
    )
    assert cfg.tts_lang == 'en-us'
    assert len(cfg.tts_voice_blend) == 2
    assert cfg.tts_voice_blend[0].name == 'jf_tebukuro'
    assert cfg.tts_voice_blend[0].weight == 0.65
    assert cfg.tts_voice_blend[0]['name'] == 'jf_tebukuro'
    assert cfg.tts_voice_blend[1].name == 'af_bella'

    # Invalid weight > 1.0
    with pytest.raises(ValueError, match='voice_blend.weight'):
        FeedbackConfig(tts_voice_blend=[{'name': 'jf_tebukuro', 'weight': 1.5}])

    # Invalid item structure
    with pytest.raises(ValueError, match='feedback.tts_voice_blend items must have "name" and "weight"'):
        FeedbackConfig(tts_voice_blend=[{'invalid': 123}])


def test_ascend_config_uses_environment_variable_names_and_api_paths():
    cfg = AscendConfig(base_url_env='ASCEND_TEST_URL', api_token_env='ASCEND_TEST_TOKEN',
                       timeout_seconds=3.5, health_path='/healthz', command_path='/commands')

    assert cfg.base_url_env == 'ASCEND_TEST_URL'
    assert cfg.api_token_env == 'ASCEND_TEST_TOKEN'
    assert cfg.health_path == '/healthz'
    assert cfg.command_path == '/commands'

    with pytest.raises(ValueError, match="start with '/'"):
        AscendConfig(health_path='healthz')
