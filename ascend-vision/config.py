"""Strict, typed settings shared by the Phase 1 components."""
from dataclasses import dataclass, field, fields, replace
import math
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def number(name, value, minimum, maximum=None, integer=False):
    valid_type = type(value) is int if integer else type(value) in (int, float)
    if (not valid_type or not math.isfinite(value) or value < minimum
            or (maximum is not None and value > maximum)):
        raise ValueError(f'{name} must be a finite {"integer" if integer else "number"}'
                         f' >= {minimum}' + (f' and <= {maximum}' if maximum is not None else ''))


@dataclass(frozen=True)
class CameraConfig:
    index: int = 0
    backend: str = 'auto'
    width: int = 640
    height: int = 480
    fps: int = 30
    read_timeout_seconds: float = 5.0
    shutdown_timeout_seconds: float = 3.0

    def __post_init__(self):
        for name, minimum in [('index', 0), ('width', 1), ('height', 1), ('fps', 1)]:
            number(f'camera.{name}', getattr(self, name), minimum, integer=True)
        for name in ('read_timeout_seconds', 'shutdown_timeout_seconds'):
            number(f'camera.{name}', getattr(self, name), .01)
        if self.backend not in ('auto', 'dshow', 'msmf', 'v4l2', 'avfoundation'):
            raise ValueError('camera.backend must be auto, dshow, msmf, v4l2 or avfoundation')


@dataclass(frozen=True)
class DetectorConfig:
    model: Path = Path('models/yolo26n.pt')
    confidence: float = .5
    image_size: int = 640
    device: str = 'cpu'
    every_n_frames: int = 1
    cpu_threads: int = 4

    def __post_init__(self):
        if not isinstance(self.model, (str, Path)) or not str(self.model).strip():
            raise ValueError('detector.model must be a local model path')
        if '://' in str(self.model) or Path(self.model).suffix != '.pt':
            raise ValueError('detector.model must be a local .pt model path')
        object.__setattr__(self, 'model', Path(self.model))
        number('detector.confidence', self.confidence, 0, 1)
        number('detector.image_size', self.image_size, 32, 4096, integer=True)
        if self.image_size % 32:
            raise ValueError('detector.image_size must be a multiple of 32')
        number('detector.every_n_frames', self.every_n_frames, 1, integer=True)
        number('detector.cpu_threads', self.cpu_threads, 1, 256, integer=True)
        if not isinstance(self.device, str) or not self.device.strip():
            raise ValueError('detector.device must be a nonempty string, e.g. cpu')


@dataclass(frozen=True)
class RuntimeConfig:
    preview: bool = True
    stats_interval_seconds: float = 5.
    target_fps: float = 15.

    def __post_init__(self):
        if type(self.preview) is not bool:
            raise ValueError('runtime.preview must be a YAML boolean')
        number('runtime.stats_interval_seconds', self.stats_interval_seconds, .1)
        number('runtime.target_fps', self.target_fps, .1)


@dataclass(frozen=True)
class CompanionContextConfig:
    """Local laptop context; disabled until explicitly enabled by the owner."""
    enabled: bool = False
    share_with_phone: bool = False
    proactive_enabled: bool = False
    shadow_mode_enabled: bool = False
    mode: str = 'quiet'
    break_suggestion_enabled: bool = False
    desk_checkin_enabled: bool = False
    agent_needs_input_enabled: bool = False
    break_interval_minutes: int = 50
    desk_absence_minutes: int = 5
    desktop_poll_seconds: float = 2.0
    idle_after_seconds: float = 60.0
    absence_dwell_seconds: float = 10.0
    return_dwell_seconds: float = 3.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('companion_context.enabled must be a YAML boolean')
        for name in ('share_with_phone', 'proactive_enabled', 'shadow_mode_enabled',
                     'break_suggestion_enabled', 'desk_checkin_enabled',
                     'agent_needs_input_enabled'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'companion_context.{name} must be a YAML boolean')
        if self.proactive_enabled and not self.enabled:
            raise ValueError('proactive companion requires companion_context.enabled')
        if self.agent_needs_input_enabled and not (self.proactive_enabled or self.shadow_mode_enabled):
            raise ValueError('agent needs-input rule requires proactive or shadow mode')
        if self.shadow_mode_enabled and not self.enabled:
            raise ValueError('companion shadow mode requires companion_context.enabled')
        if self.share_with_phone and not self.enabled:
            raise ValueError('companion_context.share_with_phone requires companion_context.enabled')
        if self.mode not in ('quiet', 'companion', 'focus_coach'):
            raise ValueError('companion_context.mode must be quiet, companion, or focus_coach')
        number('companion_context.break_interval_minutes', self.break_interval_minutes, 10, 180, integer=True)
        number('companion_context.desk_absence_minutes', self.desk_absence_minutes, 1, 60, integer=True)
        number('companion_context.desktop_poll_seconds', self.desktop_poll_seconds, .5, 10)
        number('companion_context.idle_after_seconds', self.idle_after_seconds, 1, 3600)
        number('companion_context.absence_dwell_seconds', self.absence_dwell_seconds, 1, 60)
        number('companion_context.return_dwell_seconds', self.return_dwell_seconds, .5, 30)


@dataclass(frozen=True)
class HoldConfig:
    """Hold confirmation and future alert eligibility."""
    threshold_frames: int = 12
    proximity_px: float = 60.
    cooldown_seconds: float = 45.
    distance_metric: str = 'center'
    max_observation_gap_seconds: float = 1.
    posture_enabled: bool = True
    call_proximity_px: float = 120.0
    trajectory_smoothing_frames: int = 5

    def __post_init__(self):
        number('hold.threshold_frames', self.threshold_frames, 1, integer=True)
        number('hold.proximity_px', self.proximity_px, 0)
        number('hold.cooldown_seconds', self.cooldown_seconds, 0)
        number('hold.max_observation_gap_seconds', self.max_observation_gap_seconds, .01)
        if self.distance_metric not in ('center', 'box'):
            raise ValueError('hold.distance_metric must be center or box')
        if type(self.posture_enabled) is not bool:
            raise ValueError('hold.posture_enabled must be a YAML boolean')
        number('hold.call_proximity_px', self.call_proximity_px, 1.0)
        number('hold.trajectory_smoothing_frames', self.trajectory_smoothing_frames, 1, 60, integer=True)



@dataclass(frozen=True)
class HandConfig:
    model: Path = Path('models/hand_landmarker.task')
    num_hands: int = 2
    detection_confidence: float = .5
    presence_confidence: float = .5
    tracking_confidence: float = .5

    def __post_init__(self):
        if (not isinstance(self.model, (str, Path)) or '://' in str(self.model)
                or Path(self.model).suffix != '.task'):
            raise ValueError('hands.model must be a local .task model path')
        object.__setattr__(self, 'model', Path(self.model))
        number('hands.num_hands', self.num_hands, 1, 2, integer=True)
        for name in ('detection_confidence', 'presence_confidence', 'tracking_confidence'):
            number(f'hands.{name}', getattr(self, name), 0, 1)


@dataclass(frozen=True)
class FaceConfig:
    model: Path = Path('models/face_landmarker.task')
    num_faces: int = 1
    detection_confidence: float = .5
    presence_confidence: float = .5
    tracking_confidence: float = .5

    def __post_init__(self):
        if (not isinstance(self.model, (str, Path)) or '://' in str(self.model)
                or Path(self.model).suffix != '.task'):
            raise ValueError('face.model must be a local .task model path')
        object.__setattr__(self, 'model', Path(self.model))
        number('face.num_faces', self.num_faces, 1, 10, integer=True)
        for name in ('detection_confidence', 'presence_confidence', 'tracking_confidence'):
            number(f'face.{name}', getattr(self, name), 0, 1)


@dataclass(frozen=True)
class DrowsinessConfig:
    ear_threshold: float = .22
    closed_consec_frames: int = 15
    cooldown_seconds: float = 30.0
    calibration_frames: int = 30
    adaptive_threshold_ratio: float = 0.70  # threshold = baseline_ear * ratio

    def __post_init__(self):
        number('drowsiness.ear_threshold', self.ear_threshold, .01, 1.0)
        number('drowsiness.closed_consec_frames', self.closed_consec_frames, 1, 300, integer=True)
        number('drowsiness.cooldown_seconds', self.cooldown_seconds, 0)
        number('drowsiness.calibration_frames', self.calibration_frames, 0, 300, integer=True)
        number('drowsiness.adaptive_threshold_ratio', self.adaptive_threshold_ratio, 0.1, 0.99)


@dataclass(frozen=True)
class YawnConfig:
    mar_threshold: float = .65
    yawn_consec_frames: int = 20
    cooldown_seconds: float = 20.0
    calibration_frames: int = 30
    adaptive_threshold_ratio: float = 1.75  # threshold = max(config.mar_threshold, baseline_mar * ratio)

    def __post_init__(self):
        number('yawn.mar_threshold', self.mar_threshold, .01, 2.0)
        number('yawn.yawn_consec_frames', self.yawn_consec_frames, 1, 300, integer=True)
        number('yawn.cooldown_seconds', self.cooldown_seconds, 0)
        number('yawn.calibration_frames', self.calibration_frames, 0, 300, integer=True)
        number('yawn.adaptive_threshold_ratio', self.adaptive_threshold_ratio, 1.01, 5.0)


@dataclass(frozen=True)
class PostureConfig:
    enabled: bool = True
    calibration_frames: int = 30
    consec_frames: int = 30
    cooldown_seconds: float = 35.0
    head_drop_threshold: float = 0.15
    pitch_threshold_deg: float = 12.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('posture.enabled must be a YAML boolean')
        number('posture.calibration_frames', self.calibration_frames, 5, 300, integer=True)
        number('posture.consec_frames', self.consec_frames, 1, 300, integer=True)
        number('posture.cooldown_seconds', self.cooldown_seconds, 0)
        number('posture.head_drop_threshold', self.head_drop_threshold, 0.01, 2.0)
        number('posture.pitch_threshold_deg', self.pitch_threshold_deg, 1.0, 90.0)



@dataclass(frozen=True)
class StorageConfig:
    database: Path = Path('data/phone_watch.db')
    busy_timeout_seconds: float = 5.
    heartbeat_seconds: float = 5.
    update_seconds: float = 1.

    def __post_init__(self):
        if (not isinstance(self.database, (str, Path)) or not str(self.database).strip()
                or '://' in str(self.database) or str(self.database) == ':memory:'):
            raise ValueError('storage.database must be a local file path')
        object.__setattr__(self, 'database', Path(self.database))
        for name in ('busy_timeout_seconds', 'heartbeat_seconds', 'update_seconds'):
            number(f'storage.{name}', getattr(self, name), .01)


@dataclass(frozen=True)
class SessionConfig:
    initial_mode: str = 'background'
    tray: bool = True
    hotkey_enabled: bool = True
    hotkey: str = 'ctrl+alt+f'

    def __post_init__(self):
        if self.initial_mode not in ('background', 'focus'):
            raise ValueError('sessions.initial_mode must be background or focus')
        for name in ('tray', 'hotkey_enabled'):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f'sessions.{name} must be a YAML boolean')
        if not isinstance(self.hotkey, str) or not self.hotkey.strip():
            raise ValueError('sessions.hotkey must be a nonempty hotkey string')


@dataclass(frozen=True)
class VoiceBlendItem:
    name: str
    weight: float

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('voice_blend.name must be a nonempty voice name string')
        number('voice_blend.weight', self.weight, 0.0, 1.0)

    def __getitem__(self, item):
        if item == 'name':
            return self.name
        if item == 'weight':
            return self.weight
        raise KeyError(item)

    def get(self, item, default=None):
        if item == 'name':
            return self.name
        if item == 'weight':
            return self.weight
        return default


@dataclass(frozen=True)
class FeedbackConfig:
    enabled: bool = True
    model: str = 'gemini-3.6-flash'
    api_key_env: str = 'GEMINI_API_KEY'
    request_timeout_seconds: float = 10.
    max_age_seconds: float = 15.
    shutdown_timeout_seconds: float = 3.
    max_output_tokens: int = 120
    max_characters: int = 280
    speech_rate: int = 175
    speech_volume: float = 1.
    speech_voice: str = ''  # empty selects the system default voice
    speech_timeout_seconds: float = 20.
    tts_engine: str = 'kokoro_onnx'  # 'kokoro_onnx' or 'pyttsx3'
    tts_model_path: Path = Path('models/kokoro/kokoro-v1.0.onnx')
    tts_voices_path: Path = Path('models/kokoro/voices-v1.0.bin')
    tts_voice: str = 'af_heart'
    tts_speed: float = 1.05
    tts_lang: str = 'en-us'
    tts_voice_blend: tuple[VoiceBlendItem, ...] | list | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('feedback.enabled must be a YAML boolean')
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError('feedback.model must be a nonempty model name')
        if not isinstance(self.api_key_env, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', self.api_key_env):
            raise ValueError('feedback.api_key_env must name an environment variable')
        for name in ('request_timeout_seconds', 'max_age_seconds', 'shutdown_timeout_seconds', 'speech_timeout_seconds'):
            number(f'feedback.{name}', getattr(self, name), .1, 120)
        number('feedback.max_output_tokens', self.max_output_tokens, 32, 1024, integer=True)
        number('feedback.max_characters', self.max_characters, 40, 500, integer=True)
        number('feedback.speech_rate', self.speech_rate, 80, 300, integer=True)
        number('feedback.speech_volume', self.speech_volume, 0, 1)
        if not isinstance(self.speech_voice, str):
            raise ValueError('feedback.speech_voice must be a system voice ID string')
        if self.tts_engine not in ('kokoro_onnx', 'pyttsx3'):
            raise ValueError('feedback.tts_engine must be kokoro_onnx or pyttsx3')
        if not isinstance(self.tts_model_path, (str, Path)) or not str(self.tts_model_path).strip():
            raise ValueError('feedback.tts_model_path must be a file path')
        object.__setattr__(self, 'tts_model_path', Path(self.tts_model_path))
        if not isinstance(self.tts_voices_path, (str, Path)) or not str(self.tts_voices_path).strip():
            raise ValueError('feedback.tts_voices_path must be a file path')
        object.__setattr__(self, 'tts_voices_path', Path(self.tts_voices_path))
        if not isinstance(self.tts_voice, str) or not self.tts_voice.strip():
            raise ValueError('feedback.tts_voice must be a nonempty voice name string')
        number('feedback.tts_speed', self.tts_speed, 0.2, 3.0)
        if not isinstance(self.tts_lang, str) or not self.tts_lang.strip():
            raise ValueError('feedback.tts_lang must be a nonempty language code string')
        if self.tts_voice_blend is not None:
            if not isinstance(self.tts_voice_blend, (list, tuple)):
                raise ValueError('feedback.tts_voice_blend must be a list of voice blend items')
            blends = []
            for item in self.tts_voice_blend:
                if isinstance(item, dict):
                    if 'name' not in item or 'weight' not in item:
                        raise ValueError('feedback.tts_voice_blend items must have "name" and "weight"')
                    blends.append(VoiceBlendItem(name=str(item['name']), weight=float(item['weight'])))
                elif isinstance(item, VoiceBlendItem):
                    blends.append(item)
                else:
                    raise ValueError('feedback.tts_voice_blend items must be dicts or VoiceBlendItem')
            object.__setattr__(self, 'tts_voice_blend', tuple(blends))



@dataclass(frozen=True)
class DashboardConfig:
    port: int = 8765
    refresh_seconds: int = 10
    default_days: int = 28
    timezone: str = 'local'
    open_browser: bool = True

    def __post_init__(self):
        number('dashboard.port', self.port, 1024, 65535, integer=True)
        number('dashboard.refresh_seconds', self.refresh_seconds, 2, 300, integer=True)
        number('dashboard.default_days', self.default_days, 1, 366, integer=True)
        if type(self.open_browser) is not bool:
            raise ValueError('dashboard.open_browser must be a YAML boolean')
        from dashboard_stats import get_zone
        get_zone(self.timezone)


@dataclass(frozen=True)
class VoiceCommandConfig:
    enabled: bool = True
    model_size: str = 'tiny.en'
    compute_type: str = 'int8'
    device: str = 'cpu'
    device_index: int | None = None
    cpu_threads: int = 2
    energy_threshold: float = 0.005
    silence_duration_seconds: float = 0.8
    min_speech_duration_seconds: float = 0.3
    max_speech_duration_seconds: float = 10.0
    mute_duration_seconds: float = 300.0
    conversational_mode: bool = True
    max_reply_words: int = 25
    chat_cooldown_seconds: float = 5.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('voice_commands.enabled must be a YAML boolean')
        if not isinstance(self.model_size, str) or not self.model_size.strip():
            raise ValueError('voice_commands.model_size must be a nonempty string')
        if not isinstance(self.compute_type, str) or not self.compute_type.strip():
            raise ValueError('voice_commands.compute_type must be a nonempty string')
        if not isinstance(self.device, str) or not self.device.strip():
            raise ValueError('voice_commands.device must be a nonempty string')
        if self.device_index is not None and (not isinstance(self.device_index, int) or self.device_index < 0):
            raise ValueError('voice_commands.device_index must be a non-negative integer or null')
        number('voice_commands.cpu_threads', self.cpu_threads, 1, 64, integer=True)
        number('voice_commands.energy_threshold', self.energy_threshold, 0.00001, 1.0)
        number('voice_commands.silence_duration_seconds', self.silence_duration_seconds, 0.1, 10.0)
        number('voice_commands.min_speech_duration_seconds', self.min_speech_duration_seconds, 0.1, 5.0)
        number('voice_commands.max_speech_duration_seconds', self.max_speech_duration_seconds, 1.0, 60.0)
        number('voice_commands.mute_duration_seconds', self.mute_duration_seconds, 1.0, 86400.0)
        if type(self.conversational_mode) is not bool:
            raise ValueError('voice_commands.conversational_mode must be a YAML boolean')
        number('voice_commands.max_reply_words', self.max_reply_words, 5, 100, integer=True)
        number('voice_commands.chat_cooldown_seconds', self.chat_cooldown_seconds, 0.5, 300.0)


@dataclass(frozen=True)
class LLMConfig:
    groq_model: str = 'llama-3.1-8b-instant'
    cerebras_model: str = 'llama3.1-8b'
    gemini_model: str = 'gemini-3.6-flash'
    groq_api_key_env: str = 'GROQ_API_KEY'
    cerebras_api_key_env: str = 'CEREBRAS_API_KEY'
    gemini_api_key_env: str = 'GEMINI_API_KEY'
    default_max_tokens: int = 60

    def __post_init__(self):
        if not isinstance(self.groq_model, str) or not self.groq_model.strip():
            raise ValueError('llm.groq_model must be a nonempty string')
        if not isinstance(self.cerebras_model, str) or not self.cerebras_model.strip():
            raise ValueError('llm.cerebras_model must be a nonempty string')
        if not isinstance(self.gemini_model, str) or not self.gemini_model.strip():
            raise ValueError('llm.gemini_model must be a nonempty string')
        for env_var in (self.groq_api_key_env, self.cerebras_api_key_env, self.gemini_api_key_env):
            if not isinstance(env_var, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', env_var):
                raise ValueError(f'llm environment variable name must be valid: {env_var}')
        number('llm.default_max_tokens', self.default_max_tokens, 10, 1024, integer=True)


@dataclass(frozen=True)
class AscendConfig:
    enabled: bool = True
    base_url_env: str = 'ASCEND_BASE_URL'
    api_token_env: str = 'ASCEND_API_TOKEN'
    device_id_env: str = 'ASCEND_DEVICE_ID'
    character_id_env: str = 'ASCEND_CHARACTER_ID'
    timeout_seconds: float = 5.
    health_path: str = '/api/integration/health'
    command_path: str = '/api/integration/command'
    event_path: str = '/api/integration/event'

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('ascend.enabled must be a YAML boolean')
        for name in ('base_url_env', 'api_token_env', 'device_id_env', 'character_id_env'):
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
                raise ValueError(f'ascend.{name} must name an environment variable')
        number('ascend.timeout_seconds', self.timeout_seconds, .1, 120)
        for name in ('health_path', 'command_path', 'event_path'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.startswith('/'):
                raise ValueError(f'ascend.{name} must start with \'/\'')


@dataclass(frozen=True)
class ScreenAuditConfig:
    enabled: bool = False
    interval_seconds: float = 1800.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('screen_audit.enabled must be a YAML boolean')
        number('screen_audit.interval_seconds', self.interval_seconds, 1.0)


@dataclass(frozen=True)
class PhoneChatConfig:
    """Opt-in outbound phone worker; credentials and URL are environment-only."""
    enabled: bool = False
    voice_upload_enabled: bool = False
    core_url_env: str = 'ASCEND_PHONE_CORE_URL'
    worker_token_env: str = 'ASCEND_PHONE_WORKER_TOKEN'
    owner_id_env: str = 'ASCEND_PHONE_OWNER_ID'
    poll_interval_seconds: float = 2.0
    lease_renew_interval_seconds: float = 20.0
    request_timeout_seconds: float = 15.0
    shutdown_timeout_seconds: float = 3.0

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('phone_chat.enabled must be a YAML boolean')
        if type(self.voice_upload_enabled) is not bool:
            raise ValueError('phone_chat.voice_upload_enabled must be a YAML boolean')
        if self.voice_upload_enabled and not self.enabled:
            raise ValueError('phone_chat.voice_upload_enabled requires phone_chat.enabled')
        for name in ('core_url_env', 'worker_token_env', 'owner_id_env'):
            if not isinstance(getattr(self, name), str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', getattr(self, name)):
                raise ValueError(f'phone_chat.{name} must name an environment variable')
        number('phone_chat.poll_interval_seconds', self.poll_interval_seconds, .1, 60)
        number('phone_chat.lease_renew_interval_seconds', self.lease_renew_interval_seconds, .1, 45)
        number('phone_chat.request_timeout_seconds', self.request_timeout_seconds, .1, 120)
        number('phone_chat.shutdown_timeout_seconds', self.shutdown_timeout_seconds, .1, 30)


@dataclass(frozen=True)
class BrowserAutomationConfig:
    """Opt-in local browser research with conservative task limits."""
    enabled: bool = False
    b3_enabled: bool = False
    remote_enabled: bool = False
    remote_writes_enabled: bool = False
    remote_scopes: tuple[dict, ...] = ()
    provider: str = 'gemini'
    scope_mode: str = 'public_research'
    profile_mode: str = 'ephemeral'
    max_decisions: int = 20
    max_actions: int = 30
    max_pages: int = 3
    max_queued_tasks: int = 4
    task_timeout_seconds: int = 180
    metrics_enabled: bool = False
    metrics_retention_days: int = 30
    metrics_max_runs: int = 10_000
    pilot_enabled: bool = False
    pilot_price_table_path: str | None = None
    pilot_token_bound_reviewed: bool = False
    pilot_b2_live_accepted: bool = False
    pilot_voice_completion_confirmed: bool = False
    pilot_daily_budget_usd_micros: int = 1_000_000
    pilot_total_budget_usd_micros: int = 5_000_000
    enabled_routines: tuple[dict, ...] = ()

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError('browser_automation.enabled must be a YAML boolean')
        if type(self.b3_enabled) is not bool:
            raise ValueError('browser_automation.b3_enabled must be a YAML boolean')
        if type(self.remote_enabled) is not bool or type(self.remote_writes_enabled) is not bool:
            raise ValueError('browser_automation remote settings must be YAML booleans')
        if type(self.metrics_enabled) is not bool:
            raise ValueError('browser_automation.metrics_enabled must be a YAML boolean')
        if type(self.pilot_enabled) is not bool:
            raise ValueError('browser_automation.pilot_enabled must be a YAML boolean')
        if type(self.pilot_token_bound_reviewed) is not bool:
            raise ValueError('browser_automation.pilot_token_bound_reviewed must be a YAML boolean')
        if type(self.pilot_b2_live_accepted) is not bool or type(self.pilot_voice_completion_confirmed) is not bool:
            raise ValueError('browser_automation pilot acceptance settings must be YAML booleans')
        if self.b3_enabled and not self.enabled:
            raise ValueError('browser_automation.b3_enabled requires browser automation to be enabled')
        if self.remote_enabled and not self.enabled:
            raise ValueError('browser_automation.remote_enabled requires browser automation to be enabled')
        if self.remote_writes_enabled and not (self.remote_enabled and self.b3_enabled):
            raise ValueError('browser_automation.remote_writes_enabled requires remote_enabled and b3_enabled')
        if self.provider not in {'gemini', 'cerebras', 'groq'}:
            raise ValueError('browser_automation.provider must be gemini, cerebras or groq')
        if self.scope_mode != 'public_research':
            raise ValueError('browser_automation.scope_mode must be public_research in this release')
        if self.profile_mode != 'ephemeral':
            raise ValueError('browser_automation.profile_mode must be ephemeral in this release')
        if not isinstance(self.remote_scopes, (tuple, list)) or len(self.remote_scopes) > 10:
            raise ValueError('browser_automation.remote_scopes must contain at most 10 scope objects')
        scope_ids = set()
        normalized_scopes = []
        for scope in self.remote_scopes:
            if not isinstance(scope, dict) or scope.keys() - {'scope_id', 'origin', 'actions', 'profile_id', 'version'}:
                raise ValueError('browser_automation.remote_scopes contains an invalid scope object')
            scope_id, origin, actions = scope.get('scope_id'), scope.get('origin'), scope.get('actions')
            version, profile_id = scope.get('version', 1), scope.get('profile_id')
            if not isinstance(scope_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', scope_id):
                raise ValueError('browser_automation remote scope_id is invalid')
            if scope_id in scope_ids:
                raise ValueError('browser_automation remote scope_id values must be unique')
            scope_ids.add(scope_id)
            if not isinstance(origin, str):
                raise ValueError('browser_automation remote scope origin is invalid')
            parsed_origin = urlsplit(origin)
            try:
                port = parsed_origin.port
            except ValueError as exc:
                raise ValueError('browser_automation remote scope origin is invalid') from exc
            if (parsed_origin.scheme not in {'https', 'http'} or not parsed_origin.hostname
                    or parsed_origin.username or parsed_origin.password
                    or parsed_origin.path not in {'', '/'} or parsed_origin.query or parsed_origin.fragment
                    or (port is not None and port != (443 if parsed_origin.scheme == 'https' else 80))
                    or (parsed_origin.scheme != 'https' and parsed_origin.hostname not in {'localhost', '127.0.0.1', '::1'})):
                raise ValueError('browser_automation remote scopes must use public HTTPS origins')
            if (not isinstance(actions, (list, tuple)) or len(actions) > 5
                    or any(item not in {'fill', 'select', 'click'} for item in actions)
                    or len(set(actions)) != len(actions)):
                raise ValueError('browser_automation remote scope actions are invalid')
            if type(version) is not int or version < 1:
                raise ValueError('browser_automation remote scope version must be positive')
            if profile_id is not None and (not isinstance(profile_id, str)
                                           or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', profile_id)):
                raise ValueError('browser_automation remote scope profile_id is invalid')
            normalized_scopes.append({
                'scope_id': scope_id,
                'origin': f'{parsed_origin.scheme.lower()}://{parsed_origin.hostname.rstrip(".").lower()}',
                'actions': tuple(actions), 'profile_id': profile_id, 'version': version,
            })
        if normalized_scopes and not self.remote_writes_enabled:
            raise ValueError('browser_automation.remote_scopes require remote_writes_enabled')
        object.__setattr__(self, 'remote_scopes', tuple(normalized_scopes))
        for name, maximum in (
            ('max_decisions', 20), ('max_actions', 30), ('max_pages', 3),
            ('max_queued_tasks', 4), ('task_timeout_seconds', 180),
        ):
            number(f'browser_automation.{name}', getattr(self, name), 1, maximum, integer=True)
        number('browser_automation.metrics_retention_days', self.metrics_retention_days, 1, 30, integer=True)
        number('browser_automation.metrics_max_runs', self.metrics_max_runs, 1, 10_000, integer=True)
        if self.pilot_price_table_path is not None and (
                not isinstance(self.pilot_price_table_path, str) or not self.pilot_price_table_path.strip()
                or len(self.pilot_price_table_path) > 300):
            raise ValueError('browser_automation.pilot_price_table_path is invalid')
        number('browser_automation.pilot_daily_budget_usd_micros', self.pilot_daily_budget_usd_micros,
               1, 1_000_000, integer=True)
        number('browser_automation.pilot_total_budget_usd_micros', self.pilot_total_budget_usd_micros,
               1, 5_000_000, integer=True)
        if self.pilot_enabled and not (self.enabled and self.metrics_enabled and self.pilot_price_table_path
                                       and self.pilot_token_bound_reviewed and self.pilot_b2_live_accepted
                                       and self.pilot_voice_completion_confirmed):
            raise ValueError('browser_automation.pilot_enabled requires browser and metrics enabled, B2 live and voice acceptance, a reviewed price table, and an accepted conservative token bound')
        if not isinstance(self.enabled_routines, (tuple, list)) or len(self.enabled_routines) > 20:
            raise ValueError('browser_automation.enabled_routines must contain at most 20 exact routine references')
        routine_refs = []
        for item in self.enabled_routines:
            if (not isinstance(item, dict) or item.keys() != {'routine_id', 'version', 'digest'}
                    or not isinstance(item['routine_id'], str)
                    or not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', item['routine_id'])
                    or type(item['version']) is not int or item['version'] < 1
                    or not isinstance(item['digest'], str)
                    or not re.fullmatch(r'[0-9a-f]{64}', item['digest'])):
                raise ValueError('browser_automation.enabled_routines contains an invalid exact reference')
            key = (item['routine_id'], item['version'])
            if any((prior['routine_id'], prior['version']) == key for prior in routine_refs):
                raise ValueError('browser_automation.enabled_routines contains a duplicate version')
            routine_refs.append(dict(item))
        if routine_refs and not self.enabled:
            raise ValueError('browser_automation.enabled_routines requires browser automation to be enabled')
        object.__setattr__(self, 'enabled_routines', tuple(routine_refs))


@dataclass(frozen=True)
class Config:
    camera: CameraConfig = field(default_factory=CameraConfig)
    detector: DetectorConfig = field(default_factory=DetectorConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    companion_context: CompanionContextConfig = field(default_factory=CompanionContextConfig)
    hold: HoldConfig = field(default_factory=HoldConfig)
    hands: HandConfig = field(default_factory=HandConfig)
    face: FaceConfig = field(default_factory=FaceConfig)
    drowsiness: DrowsinessConfig = field(default_factory=DrowsinessConfig)
    yawn: YawnConfig = field(default_factory=YawnConfig)
    posture: PostureConfig = field(default_factory=PostureConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    sessions: SessionConfig = field(default_factory=SessionConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    voice_commands: VoiceCommandConfig = field(default_factory=VoiceCommandConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    ascend: AscendConfig = field(default_factory=AscendConfig)
    screen_audit: ScreenAuditConfig = field(default_factory=ScreenAuditConfig)
    phone_chat: PhoneChatConfig = field(default_factory=PhoneChatConfig)
    browser_automation: BrowserAutomationConfig = field(default_factory=BrowserAutomationConfig)


def load_config(path: Path) -> Config:
    path = Path(path).resolve()
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f'Cannot read config {path}: {exc}') from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError('config must contain a YAML mapping')
    sections = {'camera': CameraConfig, 'detector': DetectorConfig,
                'runtime': RuntimeConfig, 'companion_context': CompanionContextConfig,
                'hold': HoldConfig, 'hands': HandConfig,
                'face': FaceConfig, 'drowsiness': DrowsinessConfig, 'yawn': YawnConfig,
                'posture': PostureConfig,
                'storage': StorageConfig, 'sessions': SessionConfig, 'feedback': FeedbackConfig,
                'dashboard': DashboardConfig, 'voice_commands': VoiceCommandConfig,
                'llm': LLMConfig, 'ascend': AscendConfig,
                 'screen_audit': ScreenAuditConfig, 'phone_chat': PhoneChatConfig,
                 'browser_automation': BrowserAutomationConfig}
    if data.keys() - sections.keys():
        raise ValueError(f'Unknown config sections: {data.keys() - sections.keys()}')
    values = {}
    for name, cls in sections.items():
        content = data.get(name, {})
        if not isinstance(content, dict):
            raise ValueError(f'config.{name} must be a mapping')
        unknown = content.keys() - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f'Unknown {name} settings: {unknown}')
        values[name] = cls(**content)
    cfg = Config(**values)
    return replace(cfg, detector=replace(cfg.detector,
                   model=(path.parent / cfg.detector.model).resolve()),
                   hands=replace(cfg.hands, model=(path.parent / cfg.hands.model).resolve()),
                   face=replace(cfg.face, model=(path.parent / cfg.face.model).resolve()),
                   storage=replace(cfg.storage, database=(path.parent / cfg.storage.database).resolve()),
                   feedback=replace(
                       cfg.feedback,
                       tts_model_path=(path.parent / cfg.feedback.tts_model_path).resolve(),
                       tts_voices_path=(path.parent / cfg.feedback.tts_voices_path).resolve()
                   ))
