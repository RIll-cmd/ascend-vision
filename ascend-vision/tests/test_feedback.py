import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from config import FeedbackConfig
from feedback import FeedbackService, GeminiRoaster, RoastContext
from speech import SpeechOutcome

CONTEXT = RoastContext(7, 2, 34., '14:20')


def wait_result(service):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        results = service.drain()
        if results:
            return results[0]
        time.sleep(.005)
    pytest.fail('Feedback result did not arrive')


def test_api_receives_only_allowlisted_metadata():
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(candidates=[SimpleNamespace(finish_reason='STOP')], text='Your phone has promoted itself to project manager.')
    roast = GeminiRoaster(FeedbackConfig(), client=client).generate(CONTEXT)
    assert roast.startswith('Your phone')
    args = client.models.generate_content.call_args.kwargs
    assert json.loads(args['contents']) == {'pickups_today': 7, 'pickups_this_session': 2,
        'session_duration_minutes': 34., 'time_of_day': '14:20'}
    assert 'tools' not in args
    assert set(args) == {'model', 'contents', 'config'}


@pytest.mark.parametrize('text,status', [('', 'STOP'), ('hello', 'MAX_TOKENS'),
                                        ('x' * 400, 'STOP')])
def test_bad_response_is_not_spoken(text, status):
    client = Mock()
    client.models.generate_content.return_value = SimpleNamespace(candidates=[SimpleNamespace(finish_reason=status)], text=text)
    with pytest.raises(ValueError):
        GeminiRoaster(FeedbackConfig(), client=client).generate(CONTEXT)


def test_background_is_silent_and_focus_cooldown_does_not_block_logging():
    generator = Mock()
    generator.generate.return_value = 'Your phone is not on the payroll.'
    speaker = Mock()
    speaker.speak.return_value = SpeechOutcome(True, True)
    service = FeedbackService(FeedbackConfig(), 45, generator=generator, speaker=speaker)
    service.start()
    try:
        assert not service.submit(1, 1, CONTEXT)
        service.set_session(2)
        assert service.submit(2, 2, CONTEXT)
        assert not service.submit(2, 2, CONTEXT)
        result = wait_result(service)
        assert result.event_id == 2 and result.spoken
        assert not service.submit(3, 2, CONTEXT)
        assert generator.generate.call_count == 1
    finally:
        service.close()


def test_generation_does_not_block_submit_and_cancelled_result_never_speaks():
    entered, release = threading.Event(), threading.Event()
    def generate(context):
        entered.set()
        assert release.wait(2)
        return 'Put it down.'
    generator = Mock()
    generator.generate.side_effect = generate
    speaker = Mock()
    service = FeedbackService(FeedbackConfig(), 0, generator=generator, speaker=speaker)
    service.start(); service.set_session(1)
    try:
        assert service.submit(1, 1, CONTEXT)
        assert entered.wait(1)
        assert not service.submit(2, 1, CONTEXT)  # no backlog while busy
        service.set_session(None)
        service.set_session(2)  # returning to focus must not revive an older job
        release.set()
        assert wait_result(service).spoken is False
        speaker.speak.assert_not_called()
    finally:
        release.set(); service.close()


def test_generation_error_is_sanitized_and_worker_survives():
    generator = Mock()
    generator.generate.side_effect = [RuntimeError('sensitive response text'), 'Try focusing on the task.']
    speaker = Mock()
    speaker.speak.return_value = SpeechOutcome(True, True)
    service = FeedbackService(FeedbackConfig(), 0, generator=generator, speaker=speaker)
    service.start(); service.set_session(1)
    try:
        assert service.submit(1, 1, CONTEXT)
        error = wait_result(service)
        assert error.error == 'RuntimeError' and error.text is None
        assert service.submit(2, 1, CONTEXT)
        assert wait_result(service).spoken
    finally:
        service.close()


def test_disabled_feedback_does_not_create_a_worker():
    service = FeedbackService(FeedbackConfig(enabled=False), 45)
    service.start(); service.set_session(1)
    assert not service.submit(1, 1, CONTEXT)
    service.close()


def test_stale_generation_is_discarded_before_speech():
    from dataclasses import replace
    generator = Mock()
    def generate(context):
        time.sleep(.15)
        return 'Your phone can wait.'
    generator.generate.side_effect = generate
    speaker = Mock()
    service = FeedbackService(replace(FeedbackConfig(), max_age_seconds=.1), 0,
                              generator=generator, speaker=speaker)
    service.start(); service.set_session(1)
    try:
        assert service.submit(1, 1, CONTEXT)
        assert not wait_result(service).spoken
        speaker.speak.assert_not_called()
    finally:
        service.close()


def test_return_to_background_cancels_active_speech_and_keeps_partial_result():
    entered = threading.Event()
    generator = Mock()
    generator.generate.return_value = 'Your phone can wait.'
    speaker = Mock()
    def speak(text, cancelled):
        entered.set()
        deadline = time.monotonic() + 1
        while not cancelled() and time.monotonic() < deadline:
            time.sleep(.005)
        assert cancelled()
        return SpeechOutcome(True, False)
    speaker.speak.side_effect = speak
    service = FeedbackService(FeedbackConfig(), 0, generator=generator, speaker=speaker)
    service.start(); service.set_session(1)
    try:
        assert service.submit(1, 1, CONTEXT)
        assert entered.wait(1)
        service.set_session(None)
        result = wait_result(service)
        assert result.spoken and not result.completed
        assert result.text == 'Your phone can wait.'
    finally:
        service.close()


def test_prompt_strategy_selection_for_all_event_types():
    from feedback import get_instruction

    microsleep_ctx = RoastContext(2, 1, 10.0, '15:30', event_type='drowsiness_microsleep')
    inst_micro = get_instruction(microsleep_ctx)
    assert 'CRITICAL SAFETY ALERT' in inst_micro
    assert 'microsleep' in inst_micro.lower()

    yawn_ctx = RoastContext(3, 2, 15.0, '15:35', event_type='yawn')
    inst_yawn = get_instruction(yawn_ctx)
    assert 'FATIGUE COACH' in inst_yawn
    assert 'yawn' in inst_yawn.lower()

    phone_ctx = RoastContext(4, 3, 20.0, '15:40', event_type='phone_held', posture='texting')
    inst_phone = get_instruction(phone_ctx)
    assert 'DISTRACTION ROASTER' in inst_phone
    assert 'texting' in inst_phone.lower()

    slouch_ctx = RoastContext(5, 4, 12.0, '15:45', event_type='slouch')
    inst_slouch = get_instruction(slouch_ctx)
    assert 'POSTURE COACH' in inst_slouch
    assert 'spine' in inst_slouch.lower() or 'shrimp' in inst_slouch.lower()


def test_offline_fallback_alerts():
    from feedback import OfflineRoaster, get_fallback_alert

    roaster = OfflineRoaster()
    micro_alert = roaster.generate(RoastContext(1, 1, 5.0, '10:00', event_type='drowsiness_microsleep'))
    assert any(cue in micro_alert.lower() for cue in ('wake up', 'eyes', 'microsleep', 'danger'))

    yawn_alert = roaster.generate(RoastContext(1, 1, 5.0, '10:00', event_type='yawn'))
    assert any(cue in yawn_alert.lower() for cue in ('yawn', 'coffee', 'fatigue', 'sleep'))

    texting_alert = roaster.generate(RoastContext(1, 1, 5.0, '10:00', event_type='phone_held', posture='texting'))
    assert 'texting' in texting_alert.lower() or 'typing' in texting_alert.lower()

    slouch_alert = roaster.generate(RoastContext(1, 1, 5.0, '10:00', event_type='slouch'))
    assert any(cue in slouch_alert.lower() for cue in ('shrimp', 'spine', 'posture', 'back', 'hunching'))


