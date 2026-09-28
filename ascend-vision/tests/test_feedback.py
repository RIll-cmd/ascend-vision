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


def test_feedback_reports_pending_legacy_speech_for_companion_arbitration():
    service = FeedbackService(FeedbackConfig(), 45, generator=Mock(), speaker=Mock())
    service.enabled = True
    try:
        assert service.speak_guarded_announcement("shadow", lambda: True)
        assert service.has_pending_noncompanion_speech() is False
        assert service.speak_announcement("legacy announcement")
        assert service.has_pending_noncompanion_speech() is True
        service.cancel_speech()
        assert service.has_pending_noncompanion_speech() is False
    finally:
        service.close()


def test_legacy_warning_preempts_queued_companion_announcement():
    from datetime import datetime, timedelta, timezone

    from assistant.companion_policy import CompanionPolicy, CompanionPreferences
    from assistant.companion_runtime import CompanionRuntime
    from assistant.context_runtime import ContextRuntime, ObservationEnvelope
    from assistant.intervention_delivery import InterventionDelivery
    from integrations.warning_state_machine import SensoryTriggerType, WarningFirstStateMachine

    now = datetime(2026, 9, 28, 10, tzinfo=timezone.utc)
    context = ContextRuntime(
        device_id="test-device", boot_id="test-boot",
        monotonic=lambda: 100.0, now_utc=lambda: now,
    )
    for source, kind, value, sequence in (
        ("desktop_activity", "desktopActivity", "input_active", 1),
        ("desktop_activity", "foregroundCategory", "development", 2),
        ("session_runtime", "focusSession", "focus", 1),
    ):
        assert context.accept(ObservationEnvelope(
            schema_version=1, event_id=f"{source}-{sequence}", source=source,
            kind=kind, value=value, boot_id="test-boot", sequence=sequence,
            observed_at=now - timedelta(seconds=2), expires_at=now + timedelta(seconds=8),
        )) == "accepted"

    service = FeedbackService(FeedbackConfig(), 45, generator=Mock(), speaker=Mock())
    service.enabled = True
    companion = CompanionRuntime(
        context, CompanionPolicy(), InterventionDelivery(now=lambda: now),
        lambda: CompanionPreferences(
            enabled=True, mode="focus_coach", break_suggestion_enabled=True,
            focus_session_id="focus-1", focus_session_started_at=now - timedelta(minutes=51),
            local_speech_enabled=True,
            speech_queue_busy=service.has_pending_noncompanion_speech(),
        ),
        service, now=lambda: now,
    )
    try:
        tick = companion.tick()
        assert [receipt.status for receipt in tick.receipts] == ["attempted"]
        warning = WarningFirstStateMachine(feedback_service=service, time_fn=lambda: 100.0)
        warning.handle_trigger(SensoryTriggerType.PHONE, now=100.0)
        second_tick = companion.tick()

        with service._jobs.mutex:
            announcements = [job for job in service._jobs.queue if hasattr(job, "guard")]
        assert second_tick.receipts == ()
        assert second_tick.evaluation.decisions[0].reason_code == "speech_queue_busy"
        assert len(announcements) == 1
        assert announcements[0].guard is None
        assert "Phone distraction detected" in announcements[0].text
    finally:
        service.close()


def test_legacy_announcement_cancels_companion_speech_already_in_progress():
    companion_started = threading.Event()
    companion_cancelled = threading.Event()
    legacy_started = threading.Event()

    class CancellableSpeaker:
        def speak(self, text, cancelled):
            if text == "companion prompt":
                companion_started.set()
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if cancelled():
                        companion_cancelled.set()
                        return SpeechOutcome(True, False)
                    time.sleep(0.002)
            elif text == "legacy warning":
                legacy_started.set()
            return SpeechOutcome(True, True)

        def close(self):
            pass

    service = FeedbackService(FeedbackConfig(), 45, generator=Mock(), speaker=CancellableSpeaker())
    service.start()
    try:
        assert service.speak_guarded_announcement("companion prompt", lambda: True)
        assert companion_started.wait(1)
        assert service.speak_announcement("legacy warning")
        assert companion_cancelled.wait(1)
        assert legacy_started.wait(1)
    finally:
        service.close()


def test_stop_and_resume_cancels_active_companion_then_accepts_ordinary_announcement():
    companion_started = threading.Event()
    companion_cancelled = threading.Event()
    resumed_started = threading.Event()

    class CancellableSpeaker:
        def speak(self, text, cancelled):
            if text == "companion prompt":
                companion_started.set()
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    if cancelled():
                        companion_cancelled.set()
                        return SpeechOutcome(True, False)
                    time.sleep(0.002)
            elif text == "resumed announcement":
                resumed_started.set()
            return SpeechOutcome(True, True)

        def close(self):
            pass

    service = FeedbackService(FeedbackConfig(), 45, generator=Mock(), speaker=CancellableSpeaker())
    service.start()
    try:
        assert service.speak_guarded_announcement("companion prompt", lambda: True)
        assert companion_started.wait(1)
        service.cancel_speech()
        assert companion_cancelled.wait(1)
        assert service.speak_announcement("resumed announcement")
        assert resumed_started.wait(1)
    finally:
        service.close()


@pytest.mark.parametrize("speech_kind", ["chat", "screen_audit", "event_feedback"])
def test_nonannouncement_speech_preempts_queued_companion_prompt(speech_kind):
    service = FeedbackService(FeedbackConfig(), 0, generator=Mock(), speaker=Mock())
    service.enabled = True
    try:
        assert service.speak_guarded_announcement("companion prompt", lambda: True)
        if speech_kind == "chat":
            accepted = service.submit_chat("Please answer me")
        elif speech_kind == "screen_audit":
            accepted = service.submit_expression("screen-audit")
        else:
            service.set_session(7)
            accepted = service.submit(1, 7, CONTEXT)
        assert accepted
        with service._jobs.mutex:
            assert not any(getattr(job, "guard", None) is not None for job in service._jobs.queue)
    finally:
        service.close()


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


