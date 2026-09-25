"""Unit tests for voice commands, VAD, faster-whisper ingestion, and actions."""
from dataclasses import dataclass
from datetime import datetime, timezone
import queue
import time
from unittest.mock import Mock, MagicMock

import numpy as np
import pytest

from config import VoiceCommandConfig, FeedbackConfig
from feedback import FeedbackService, RoastContext, ConversationContext, OfflineRoaster
from voice_listener import (
    VoiceActivityDetector,
    VoiceCommandParser,
    VoiceCommandListener,
    VoiceCommand,
    compute_rms,
    SAMPLE_RATE
)

EPOCH = datetime(2026, 9, 6, tzinfo=timezone.utc)


def test_compute_rms():
    # 1. Empty or zero array
    assert compute_rms(np.array([], dtype=np.float32)) == 0.0
    assert compute_rms(np.zeros(1600, dtype=np.float32)) == 0.0

    # 2. Constant non-zero signal (DC offset subtracted -> 0 variance)
    constant = np.full(1600, 0.5, dtype=np.float32)
    assert compute_rms(constant) == 0.0

    # 3. Square wave: alternating +0.1 and -0.1
    square = np.array([0.1, -0.1] * 800, dtype=np.float32)
    assert abs(compute_rms(square) - 0.1) < 1e-4

    # 4. Sine wave with amplitude A=0.2 -> theoretical RMS = 0.2 / sqrt(2) ~= 0.1414
    t = np.linspace(0, 1, SAMPLE_RATE, endpoint=False, dtype=np.float32)
    sine = (0.2 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    assert abs(compute_rms(sine) - (0.2 / np.sqrt(2))) < 1e-3


def test_vad_speech_segmentation_lifecycle():
    vad = VoiceActivityDetector(
        energy_threshold=0.05,
        silence_duration_seconds=0.3,
        min_speech_duration_seconds=0.2,
        max_speech_duration_seconds=5.0,
        sample_rate=16000
    )

    # 1600 samples = 0.1s chunk
    silence_chunk = np.zeros(1600, dtype=np.float32)
    speech_chunk = np.array([0.2, -0.2] * 800, dtype=np.float32)  # RMS = 0.2 > 0.05

    # 1. Feed silence -> returns None, not active
    for _ in range(5):
        assert vad.process_chunk(silence_chunk) is None
    assert not vad.is_speech_active

    # 2. Feed 4 chunks of speech (0.4s > min 0.2s)
    for _ in range(4):
        assert vad.process_chunk(speech_chunk) is None
    assert vad.is_speech_active

    # 3. Feed 2 chunks of silence (0.2s < 0.3s threshold) -> still waiting
    assert vad.process_chunk(silence_chunk) is None
    assert vad.process_chunk(silence_chunk) is None
    assert vad.is_speech_active

    # 4. Feed 3rd chunk of silence (total 0.3s silence) -> Utterance finished!
    utterance = vad.process_chunk(silence_chunk)
    assert utterance is not None
    assert isinstance(utterance, np.ndarray)
    # Total chunks in utterance = 4 speech + 3 silence = 7 chunks = 7 * 1600 = 11200 samples
    assert len(utterance) == 7 * 1600
    assert not vad.is_speech_active


def test_vad_discards_transient_clicks():
    vad = VoiceActivityDetector(
        energy_threshold=0.05,
        silence_duration_seconds=0.3,
        min_speech_duration_seconds=0.3,  # Needs at least 0.3s
        sample_rate=16000
    )

    silence_chunk = np.zeros(1600, dtype=np.float32)
    # Only 1 chunk of noise (0.1s < 0.3s min duration)
    click_chunk = np.array([0.3, -0.3] * 800, dtype=np.float32)

    vad.process_chunk(click_chunk)
    assert vad.is_speech_active

    # Followed by 3 silence chunks (0.3s silence)
    vad.process_chunk(silence_chunk)
    vad.process_chunk(silence_chunk)
    result = vad.process_chunk(silence_chunk)

    # Discarded because total speech was only 0.1s!
    assert result is None
    assert not vad.is_speech_active


def test_vad_max_duration_cutoff():
    vad = VoiceActivityDetector(
        energy_threshold=0.05,
        max_speech_duration_seconds=0.5,
        sample_rate=16000
    )

    speech_chunk = np.array([0.2, -0.2] * 800, dtype=np.float32)

    # Feed 5 chunks of continuous speech (0.5s = max duration)
    for _ in range(4):
        assert vad.process_chunk(speech_chunk) is None

    utterance = vad.process_chunk(speech_chunk)
    assert utterance is not None
    assert len(utterance) == 5 * 1600
    assert not vad.is_speech_active


def test_voice_command_parser():
    parser = VoiceCommandParser()

    # Focus commands
    for phrase in ['start focus', 'Focus mode', 'enable focus', 'focus', 'start focus please!']:
        cmd = parser.parse(phrase)
        assert cmd is not None
        assert cmd.action == 'focus'

    # Pause / Break commands
    for phrase in ['pause', 'take a break', 'break', 'stop focus', 'stop']:
        cmd = parser.parse(phrase)
        assert cmd is not None
        assert cmd.action == 'pause'

    # Status / Summary commands
    for phrase in ['status', 'summary', 'check status', 'stats', 'report']:
        cmd = parser.parse(phrase)
        assert cmd is not None
        assert cmd.action == 'status'

    # Mute commands
    for phrase in ['mute', 'quiet', 'silence', 'mute audio', 'shut up']:
        cmd = parser.parse(phrase)
        assert cmd is not None
        assert cmd.action == 'mute'

    # Unmute commands
    for phrase in ['unmute', 'un-mute', 'resume audio', 'resume voice', 'speak again']:
        cmd = parser.parse(phrase)
        assert cmd is not None
        assert cmd.action == 'unmute'

    # Unrecognized phrases
    for noise in ['', '   ', 'hello world', 'what is the weather', 'good morning', 'testing one two']:
        assert parser.parse(noise) is None


@dataclass
class MockSegment:
    text: str


def test_voice_command_listener_transcription_and_callback():
    config = VoiceCommandConfig(
        enabled=True,
        energy_threshold=0.05,
        silence_duration_seconds=0.2,
        min_speech_duration_seconds=0.1
    )

    mock_model = Mock()
    mock_model.transcribe.return_value = ([MockSegment('start focus')], None)

    received_commands = []
    def on_command(cmd: VoiceCommand):
        received_commands.append(cmd)

    # Injected mock stream that feeds directly into listener callback
    class MockStream:
        def __init__(self, callback):
            self.callback = callback
            self.closed = False
        def start(self): pass
        def stop(self): pass
        def close(self): self.closed = True

    listener = VoiceCommandListener(
        config,
        callback=on_command,
        model=mock_model,
        stream_factory=lambda cb: MockStream(cb)
    )
    listener.start()

    try:
        # Feed 2 speech chunks + 2 silence chunks via listener._audio_callback
        speech_chunk = np.array([[0.2], [-0.2]] * 800, dtype=np.float32)
        silence_chunk = np.zeros((1600, 1), dtype=np.float32)

        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)

        # Wait for background thread to process
        deadline = time.monotonic() + 2.0
        while not received_commands and time.monotonic() < deadline:
            time.sleep(0.05)

        assert len(received_commands) == 1
        assert received_commands[0].action == 'focus'
        assert received_commands[0].raw_text == 'start focus'

        # Verify indicator state updates
        last_text, last_time = listener.last_heard
        assert last_text == 'start focus'
        assert last_time > 0.0
        assert listener.is_transcribing is False

        # Verify model.transcribe was called directly with in-memory numpy array (no file)
        assert mock_model.transcribe.called
        args, kwargs = mock_model.transcribe.call_args
        audio_arg = args[0]
        assert isinstance(audio_arg, np.ndarray)
        assert audio_arg.dtype == np.float32
        assert kwargs['language'] == 'en'
        # Recognition quality for free-form automation requests must not use
        # greedy decoding, which frequently substitutes short domain words
        # such as "log" with similar-sounding alternatives.
        assert kwargs['beam_size'] == 5
        assert kwargs['initial_prompt'] == (
            'Ascend voice commands may mention YouTube, automations, habits, '
            'and phrases such as "log my negative habit".'
        )
    finally:
        listener.close()


def test_voice_command_listener_routes_unmatched_speech_without_conversational_mode():
    config = VoiceCommandConfig(
        enabled=True,
        conversational_mode=False,
        energy_threshold=0.05,
        silence_duration_seconds=0.2,
        min_speech_duration_seconds=0.1
    )

    mock_model = Mock()
    utterance = 'Create an automation that logs my Procrastinating habit whenever phone usage starts.'
    mock_model.transcribe.return_value = ([MockSegment(utterance)], None)

    unmatched_speech = []

    class MockStream:
        def __init__(self, callback):
            self.callback = callback
        def start(self): pass
        def stop(self): pass
        def close(self): pass

    listener = VoiceCommandListener(
        config,
        unmatched_callback=unmatched_speech.append,
        model=mock_model,
        stream_factory=lambda cb: MockStream(cb)
    )
    listener.start()

    try:
        speech_chunk = np.array([[0.2], [-0.2]] * 800, dtype=np.float32)
        silence_chunk = np.zeros((1600, 1), dtype=np.float32)

        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)

        deadline = time.monotonic() + 2.0
        while not unmatched_speech and time.monotonic() < deadline:
            time.sleep(0.05)

        assert unmatched_speech == [utterance]
    finally:
        listener.close()


def test_feedback_service_muting():
    generator = Mock()
    speaker = Mock()
    speaker.speak.return_value = Mock(started=True, completed=True, error=None)

    service = FeedbackService(
        FeedbackConfig(enabled=True),
        cooldown_seconds=0.0,
        generator=generator,
        speaker=speaker
    )
    service.start()
    service.set_session(1)

    try:
        ctx = RoastContext(1, 1, 10.0, '12:00', event_type='phone_held')

        # 1. Unmuted: submit succeeds
        assert service.submit(1, 1, ctx) is True
        time.sleep(0.1)

        # 2. Mute for 10 seconds
        service.mute(10.0)
        assert service.is_muted() is True

        # When muted, submit returns False (suppressed!)
        assert service.submit(2, 1, ctx) is False

        # 3. Unmute
        service.unmute()
        assert service.is_muted() is False
        assert service.submit(3, 1, ctx) is True
    finally:
        service.close()


def test_feedback_service_announcement():
    speaker = Mock()
    speaker.speak.return_value = Mock(started=True, completed=True, error=None)

    # In background mode (session is None)
    service = FeedbackService(
        FeedbackConfig(enabled=True),
        cooldown_seconds=0.0,
        speaker=speaker
    )
    service.start()
    service.set_session(None)  # No focus session

    try:
        # Regular roasts rejected in background
        ctx = RoastContext(1, 1, 10.0, '12:00', event_type='phone_held')
        assert service.submit(1, 1, ctx) is False

        # System announcement plays regardless of session state!
        assert service.speak_announcement("Focus mode enabled. Put your distractions away.") is True

        deadline = time.monotonic() + 1.0
        while not speaker.speak.called and time.monotonic() < deadline:
            time.sleep(0.05)

        assert speaker.speak.called
        text_spoken = speaker.speak.call_args[0][0]
        assert "Focus mode enabled" in text_spoken
    finally:
        service.close()


def test_command_actions_handler(tmp_path):
    from db import Database
    from session_manager import SessionManager

    db = Database(tmp_path / "test_cmd.db")
    manager = SessionManager(db)
    manager.start(mode='background')
    feedback = Mock()

    # Simulate the exact on_voice_command callback from main.py
    def on_voice_command(cmd: VoiceCommand):
        if cmd.action == 'focus':
            manager.request('focus')
            feedback.speak_announcement("Focus mode enabled. Put your distractions away.")
        elif cmd.action == 'pause':
            manager.request('background')
            feedback.speak_announcement("Monitoring paused. Take a quick break.")
        elif cmd.action == 'status':
            sid = manager.session_id
            counts = db.session_event_counts(sid) if sid is not None else {}
            p = counts.get('phone_held', 0)
            d = counts.get('drowsiness_microsleep', 0)
            y = counts.get('yawn', 0)
            s = counts.get('slouch', 0)
            summary_text = (
                f"Session status: {p} phone pickups, {d} microsleep events, "
                f"{y} yawns, and {s} slouch events."
            )
            feedback.speak_announcement(summary_text)
        elif cmd.action == 'mute':
            feedback.mute(300.0)
            feedback.speak_announcement("Voice alerts muted for 5 minutes.")
        elif cmd.action == 'unmute':
            feedback.unmute()
            feedback.speak_announcement("Voice alerts unmuted.")

    # 1. Voice command "start focus"
    on_voice_command(VoiceCommand(action='focus', raw_text='start focus'))
    manager.process_commands()
    assert manager.mode == 'focus'
    feedback.speak_announcement.assert_called_with("Focus mode enabled. Put your distractions away.")

    # 2. Add some events to session in DB
    sid = manager.session_id
    db.start_event(sid, EPOCH, confidence=0.9, event_type='phone_held')
    db.start_event(sid, EPOCH, confidence=None, event_type='yawn')
    db.start_event(sid, EPOCH, confidence=None, event_type='slouch')

    # 3. Voice command "status"
    on_voice_command(VoiceCommand(action='status', raw_text='status report'))
    call_args = feedback.speak_announcement.call_args[0][0]
    assert "1 phone pickups" in call_args
    assert "0 microsleep events" in call_args
    assert "1 yawns" in call_args
    assert "1 slouch events" in call_args

    # 4. Voice command "pause"
    on_voice_command(VoiceCommand(action='pause', raw_text='pause'))
    manager.process_commands()
    assert manager.mode == 'background'
    feedback.speak_announcement.assert_called_with("Monitoring paused. Take a quick break.")

    # 5. Voice command "mute" & "unmute"
    on_voice_command(VoiceCommand(action='mute', raw_text='quiet please'))
    feedback.mute.assert_called_with(300.0)
    feedback.speak_announcement.assert_called_with("Voice alerts muted for 5 minutes.")

    on_voice_command(VoiceCommand(action='unmute', raw_text='unmute'))
    feedback.unmute.assert_called_once()
    feedback.speak_announcement.assert_called_with("Voice alerts unmuted.")

    manager.close()
    db.close()


def test_voice_command_listener_unmatched_routing():
    config = VoiceCommandConfig(
        enabled=True,
        energy_threshold=0.05,
        silence_duration_seconds=0.2,
        min_speech_duration_seconds=0.1,
        conversational_mode=True,
        chat_cooldown_seconds=0.5
    )

    mock_model = Mock()
    mock_model.transcribe.side_effect = [
        ([MockSegment('start focus')], None),
        ([MockSegment('Why are you roasting my posture?')], None)
    ]

    commands = []
    chats = []

    class MockStream:
        def __init__(self, callback): self.callback = callback
        def start(self): pass
        def stop(self): pass
        def close(self): pass

    listener = VoiceCommandListener(
        config,
        callback=lambda cmd: commands.append(cmd),
        unmatched_callback=lambda text: chats.append(text),
        model=mock_model,
        stream_factory=lambda cb: MockStream(cb)
    )
    listener.start()
    try:
        speech_chunk = np.array([[0.2], [-0.2]] * 800, dtype=np.float32)
        silence_chunk = np.zeros((1600, 1), dtype=np.float32)

        # 1. First utterance matches system command: "start focus"
        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)

        deadline = time.monotonic() + 2.0
        while not commands and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(commands) == 1
        assert commands[0].action == 'focus'
        assert len(chats) == 0

        # 2. Second utterance does not match system command -> routed to conversational callback
        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(speech_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)
        listener._audio_callback(silence_chunk, 1600, None, None)

        deadline = time.monotonic() + 2.0
        while not chats and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(chats) == 1
        assert "Why are you roasting" in chats[0]
    finally:
        listener.close()


def test_voice_command_listener_echo_suppression():
    config = VoiceCommandConfig(
        enabled=True,
        energy_threshold=0.05,
        silence_duration_seconds=0.2,
        min_speech_duration_seconds=0.1
    )

    mock_model = Mock()
    mock_model.transcribe.return_value = ([MockSegment('hello world')], None)

    speaking_flag = [True]  # TTS is currently actively playing audio
    received = []

    class MockStream:
        def __init__(self, callback): self.callback = callback
        def start(self): pass
        def stop(self): pass
        def close(self): pass

    listener = VoiceCommandListener(
        config,
        callback=lambda cmd: received.append(cmd),
        unmatched_callback=lambda text: received.append(text),
        is_speaking=lambda: speaking_flag[0],
        model=mock_model,
        stream_factory=lambda cb: MockStream(cb)
    )
    listener.start()
    try:
        speech_chunk = np.array([[0.2], [-0.2]] * 800, dtype=np.float32)
        silence_chunk = np.zeros((1600, 1), dtype=np.float32)

        # Feed audio while speaking_flag is True -> must be dropped
        for _ in range(3):
            listener._audio_callback(speech_chunk, 1600, None, None)
        for _ in range(3):
            listener._audio_callback(silence_chunk, 1600, None, None)

        time.sleep(0.15)
        assert not mock_model.transcribe.called
        assert len(received) == 0

        # Now TTS finishes speaking
        speaking_flag[0] = False

        for _ in range(3):
            listener._audio_callback(speech_chunk, 1600, None, None)
        for _ in range(3):
            listener._audio_callback(silence_chunk, 1600, None, None)

        deadline = time.monotonic() + 2.0
        while not received and time.monotonic() < deadline:
            time.sleep(0.05)
        assert mock_model.transcribe.called
        assert len(received) == 1
    finally:
        listener.close()


def test_feedback_service_conversational_chat():
    generator = Mock()
    generator.generate_chat.return_value = "Sit up straight, you're turning into a pretzel!"
    speaker = Mock()
    speaker.speak.return_value = Mock(started=True, completed=True, error=None)

    service = FeedbackService(
        FeedbackConfig(enabled=True),
        cooldown_seconds=0.0,
        generator=generator,
        speaker=speaker
    )
    service.start()

    try:
        ctx = ConversationContext("Why do you keep checking my posture?", mode='focus', slouch_events=3)
        assert service.submit_chat("Why do you keep checking my posture?", ctx, max_words=25, cooldown=0.5) is True

        deadline = time.monotonic() + 2.0
        while not speaker.speak.called and time.monotonic() < deadline:
            time.sleep(0.05)

        assert generator.generate_chat.called
        assert speaker.speak.called
        spoken_text = speaker.speak.call_args[0][0]
        assert "pretzel" in spoken_text

        # Rapid repeat should be suppressed by cooldown
        assert service.submit_chat("Are you listening?", ctx, max_words=25, cooldown=5.0) is False
    finally:
        service.close()


def test_feedback_service_speaks_reply_from_bound_assistant():
    from assistant.service import AssistantReply

    class Assistant:
        def respond(self, user_text, context, *, max_words):
            assert user_text == "How is focus going?"
            assert context.mode == "focus"
            assert max_words == 25
            return AssistantReply("Shared answer.", "model")

    speaker = Mock()
    speaker.speak.return_value = Mock(started=True, completed=True, error=None)
    service = FeedbackService(
        FeedbackConfig(enabled=True), cooldown_seconds=0.0,
        generator=Mock(), speaker=speaker,
    )
    service.bind_assistant(Assistant())
    service.start()
    try:
        context = ConversationContext("How is focus going?", mode="focus")
        assert service.submit_chat("How is focus going?", context, cooldown=5.0)
        deadline = time.monotonic() + 2.0
        while not speaker.speak.called and time.monotonic() < deadline:
            time.sleep(0.01)
        assert speaker.speak.call_args.args[0] == "Shared answer."
        assert not service.submit_chat("Again?", context, cooldown=5.0)
    finally:
        service.close()


def test_voice_command_config_conversational_schema():
    # Valid default
    cfg = VoiceCommandConfig()
    assert cfg.model_size == 'tiny.en'
    assert cfg.conversational_mode is True
    assert cfg.max_reply_words == 25
    assert cfg.chat_cooldown_seconds == 5.0

    # Custom valid values
    cfg2 = VoiceCommandConfig(conversational_mode=False, max_reply_words=15, chat_cooldown_seconds=2.5)
    assert cfg2.conversational_mode is False
    assert cfg2.max_reply_words == 15
    assert cfg2.chat_cooldown_seconds == 2.5

    # Invalid values
    with pytest.raises(ValueError):
        VoiceCommandConfig(conversational_mode='yes')
    with pytest.raises(ValueError):
        VoiceCommandConfig(max_reply_words=2)  # too low
    with pytest.raises(ValueError):
        VoiceCommandConfig(chat_cooldown_seconds=0.1)  # too low
