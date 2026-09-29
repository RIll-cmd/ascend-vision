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


def test_companion_context_is_opt_in_and_has_validated_sampling_defaults(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')

    cfg = load_config(path)

    assert cfg.companion_context.enabled is False
    assert cfg.companion_context.share_with_phone is False
    assert cfg.companion_context.proactive_enabled is False
    assert cfg.companion_context.mode == 'quiet'
    assert cfg.companion_context.break_suggestion_enabled is False
    assert cfg.companion_context.desk_checkin_enabled is False
    assert cfg.companion_context.desktop_poll_seconds == 2.0
    assert cfg.companion_context.absence_dwell_seconds == 10.0


def test_automatic_screen_audit_is_a_separate_opt_in(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')

    cfg = load_config(path)

    assert cfg.screen_audit.enabled is False


def test_browser_automation_is_disabled_and_uses_bounded_local_defaults(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('{}')

    cfg = load_config(path)

    assert cfg.browser_automation.enabled is False
    assert cfg.browser_automation.b3_enabled is False
    assert cfg.browser_automation.provider == 'gemini'
    assert cfg.browser_automation.max_decisions == 20
    assert cfg.browser_automation.max_actions == 30
    assert cfg.browser_automation.max_pages == 3
    assert cfg.browser_automation.task_timeout_seconds == 180
    assert cfg.browser_automation.profile_mode == 'ephemeral'


@pytest.mark.parametrize('content', [
    'browser_automation:\n  enabled: "yes"\n',
    'browser_automation:\n  b3_enabled: "yes"\n',
    'browser_automation:\n  b3_enabled: true\n',
    'browser_automation:\n  provider: other\n',
    'browser_automation:\n  max_actions: 0\n',
    'browser_automation:\n  task_timeout_seconds: 181\n',
    'browser_automation:\n  profile_mode: personal\n',
])
def test_browser_automation_rejects_unsafe_or_unbounded_settings(tmp_path, content):
    path = tmp_path / 'config.yaml'
    path.write_text(content)

    with pytest.raises(ValueError):
        load_config(path)


def test_companion_context_rejects_invalid_settings(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('companion_context:\n  enabled: "yes"\n')

    with pytest.raises(ValueError, match='companion_context.enabled'):
        load_config(path)

    path.write_text('companion_context:\n  share_with_phone: true\n')
    with pytest.raises(ValueError, match='share_with_phone requires'):
        load_config(path)

    path.write_text('companion_context:\n  enabled: true\n  share_with_phone: "yes"\n')
    with pytest.raises(ValueError, match='share_with_phone must be a YAML boolean'):
        load_config(path)


def test_proactive_companion_requires_context_and_validated_rule_settings(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('companion_context:\n  proactive_enabled: true\n')

    with pytest.raises(ValueError, match='requires companion_context.enabled'):
        load_config(path)

    path.write_text(
        'companion_context:\n'
        '  enabled: true\n'
        '  proactive_enabled: true\n'
        '  mode: focus_coach\n'
        '  break_suggestion_enabled: true\n'
        '  desk_checkin_enabled: true\n'
    )
    cfg = load_config(path)

    assert cfg.companion_context.proactive_enabled is True
    assert cfg.companion_context.mode == 'focus_coach'
    assert cfg.companion_context.break_interval_minutes == 50
    assert cfg.companion_context.desk_absence_minutes == 5


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


def test_phone_chat_worker_config_is_opt_in_and_bounded():
    from config import PhoneChatConfig

    defaults = PhoneChatConfig()
    assert defaults.enabled is False
    assert defaults.poll_interval_seconds == 2.0
    assert PhoneChatConfig(enabled=True, lease_renew_interval_seconds=40).enabled is True
    with pytest.raises(ValueError, match='phone_chat.lease_renew_interval_seconds'):
        PhoneChatConfig(lease_renew_interval_seconds=60)
    with pytest.raises(ValueError, match='phone_chat.worker_token_env'):
        PhoneChatConfig(worker_token_env='not-a-variable')


def test_load_config_accepts_optional_phone_chat_settings(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('phone_chat:\n  enabled: true\n  poll_interval_seconds: 3.5\n')

    cfg = load_config(path)

    assert cfg.phone_chat.enabled is True
    assert cfg.phone_chat.poll_interval_seconds == 3.5


def test_browser_remote_flags_are_disabled_and_require_local_features():
    from config import BrowserAutomationConfig

    defaults = BrowserAutomationConfig()
    assert defaults.remote_enabled is False
    assert defaults.remote_writes_enabled is False
    assert defaults.remote_scopes == ()
    with pytest.raises(ValueError, match='requires browser automation'):
        BrowserAutomationConfig(remote_enabled=True)
    with pytest.raises(ValueError, match='requires remote_enabled and b3_enabled'):
        BrowserAutomationConfig(enabled=True, remote_writes_enabled=True)


def test_browser_remote_scope_is_normalized_and_restricted():
    from config import BrowserAutomationConfig

    config = BrowserAutomationConfig(
        enabled=True, b3_enabled=True, remote_enabled=True, remote_writes_enabled=True,
        remote_scopes=[{'scope_id': 'account', 'origin': 'https://Example.org/',
                        'actions': ['fill'], 'profile_id': 'work', 'version': 2}],
    )
    assert config.remote_scopes[0]['origin'] == 'https://example.org'
    assert config.remote_scopes[0]['actions'] == ('fill',)
    with pytest.raises(ValueError, match='remote_scopes require remote_writes_enabled'):
        BrowserAutomationConfig(remote_scopes=[
            {'scope_id': 'account', 'origin': 'https://example.org', 'actions': []},
        ])
