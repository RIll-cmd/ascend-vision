import time
from unittest.mock import Mock

import pytest

from config import FeedbackConfig
from expression_tracker import ExpressionTracker
from feedback import (
    FeedbackService,
    OfflineRoaster,
    get_fallback_expression_reply,
    FALLBACK_EXPRESSION_REPLIES,
    EXPRESSION_PROMPT_STRATEGIES,
)
from speech import SpeechOutcome


def test_yawn_duration_and_trigger():
    tracker = ExpressionTracker(
        cooldown_seconds=45.0,
        yawn_threshold=0.60,
        yawn_duration=1.0
    )

    # Below threshold
    res = tracker.update({'jawOpen': 0.50}, timestamp=1.0)
    assert res is None
    assert tracker.current_emotion == 'neutral'
    assert tracker.get_duration('fatigue') == 0.0

    # Crosses threshold at t=2.0
    res = tracker.update({'jawOpen': 0.65}, timestamp=2.0)
    assert res is None
    assert tracker.current_emotion == 'fatigue'
    assert tracker.get_duration('fatigue') == 0.0

    # Halfway (0.5s into yawn) at t=2.5
    res = tracker.update({'jawOpen': 0.70}, timestamp=2.5)
    assert res is None
    assert tracker.get_duration('fatigue') == pytest.approx(0.5)

    # Sustained for >= 1.0s at t=3.0
    res = tracker.update({'jawOpen': 0.75}, timestamp=3.0)
    assert res == 'fatigue'
    assert tracker.last_trigger_time == 3.0

    # Continues yawning in same streak at t=3.5 -> debounced, no duplicate trigger
    res = tracker.update({'jawOpen': 0.75}, timestamp=3.5)
    assert res is None
    assert tracker.get_duration('fatigue') == pytest.approx(1.5)

    # Mouth closes at t=4.0
    res = tracker.update({'jawOpen': 0.10}, timestamp=4.0)
    assert res is None
    assert tracker.current_emotion == 'neutral'
    assert tracker.get_duration('fatigue') == 0.0


def test_smile_duration_and_trigger():
    tracker = ExpressionTracker(
        cooldown_seconds=45.0,
        smile_threshold=0.45,
        smile_duration=1.2
    )

    # Only one side smiling -> not a qualified smile
    res = tracker.update({'mouthSmileLeft': 0.60, 'mouthSmileRight': 0.20}, timestamp=1.0)
    assert res is None
    assert tracker.current_emotion == 'neutral'

    # Both sides smiling at t=2.0
    res = tracker.update({'mouthSmileLeft': 0.50, 'mouthSmileRight': 0.55}, timestamp=2.0)
    assert res is None
    assert tracker.current_emotion == 'smiling'

    # Held for 0.8s at t=2.8 (< 1.2s)
    res = tracker.update({'mouthSmileLeft': 0.52, 'mouthSmileRight': 0.58}, timestamp=2.8)
    assert res is None

    # Held for 1.2s at t=3.2 (>= 1.2s) -> triggers
    res = tracker.update({'mouthSmileLeft': 0.55, 'mouthSmileRight': 0.60}, timestamp=3.2)
    assert res == 'smiling'

    # Continues smiling -> no second trigger in same streak
    res = tracker.update({'mouthSmileLeft': 0.55, 'mouthSmileRight': 0.60}, timestamp=4.0)
    assert res is None


def test_frown_duration_and_trigger():
    tracker = ExpressionTracker(
        cooldown_seconds=45.0,
        frown_threshold=0.40,
        frown_duration=2.0
    )

    # Single brow down >= 0.40 at t=1.0
    res = tracker.update({'browDownLeft': 0.45, 'browDownRight': 0.10}, timestamp=1.0)
    assert res is None
    assert tracker.current_emotion == 'stressed'

    # Held for 1.5s at t=2.5 (< 2.0s)
    res = tracker.update({'browDownLeft': 0.50, 'browDownRight': 0.10}, timestamp=2.5)
    assert res is None

    # Held for 2.0s at t=3.0 (>= 2.0s) -> triggers
    res = tracker.update({'browDownLeft': 0.48, 'browDownRight': 0.10}, timestamp=3.0)
    assert res == 'stressed'


def test_global_cooldown_enforcement():
    tracker = ExpressionTracker(
        cooldown_seconds=45.0,
        yawn_duration=1.0,
        smile_duration=1.2,
        frown_duration=2.0
    )

    # 1. Trigger yawn at t=2.0
    tracker.update({'jawOpen': 0.80}, timestamp=1.0)
    res1 = tracker.update({'jawOpen': 0.80}, timestamp=2.0)
    assert res1 == 'fatigue'
    assert tracker.last_trigger_time == 2.0

    # Reset expression
    tracker.update({'jawOpen': 0.10}, timestamp=2.5)

    # 2. Try to trigger smile at t=10.0 (held until t=11.5)
    # Elapsed since last trigger: 11.5 - 2.0 = 9.5s < 45.0s -> suppressed by cooldown
    tracker.update({'mouthSmileLeft': 0.60, 'mouthSmileRight': 0.60}, timestamp=10.0)
    res2 = tracker.update({'mouthSmileLeft': 0.60, 'mouthSmileRight': 0.60}, timestamp=11.5)
    assert res2 is None

    # Reset smile
    tracker.update({'mouthSmileLeft': 0.10, 'mouthSmileRight': 0.10}, timestamp=12.0)

    # 3. Trigger frown at t=48.0 (held until t=50.1)
    # Elapsed since last trigger: 50.1 - 2.0 = 48.1s >= 45.0s -> triggers!
    tracker.update({'browDownLeft': 0.60, 'browDownRight': 0.60}, timestamp=48.0)
    res3 = tracker.update({'browDownLeft': 0.60, 'browDownRight': 0.60}, timestamp=50.1)
    assert res3 == 'stressed'
    assert tracker.last_trigger_time == 50.1


def test_empty_or_none_blendshapes_resets_state():
    tracker = ExpressionTracker()
    tracker.update({'jawOpen': 0.80}, timestamp=1.0)
    assert tracker.current_emotion == 'fatigue'
    assert tracker.get_duration('fatigue') == 0.0

    # Empty dict or None resets
    tracker.update({}, timestamp=2.0)
    assert tracker.current_emotion == 'neutral'
    assert tracker.get_duration('fatigue') == 0.0

    tracker.update({'jawOpen': 0.80}, timestamp=3.0)
    assert tracker.current_emotion == 'fatigue'
    tracker.update(None, timestamp=4.0)
    assert tracker.current_emotion == 'neutral'


def test_priority_fatigue_over_smile():
    tracker = ExpressionTracker(yawn_duration=1.0, smile_duration=1.0)

    # Both conditions simultaneously true
    tracker.update({'jawOpen': 0.80, 'mouthSmileLeft': 0.80, 'mouthSmileRight': 0.80}, timestamp=1.0)
    res = tracker.update({'jawOpen': 0.80, 'mouthSmileLeft': 0.80, 'mouthSmileRight': 0.80}, timestamp=2.1)
    # Fatigue has higher priority
    assert res == 'fatigue'


def test_offline_expression_replies():
    for emotion in ('fatigue', 'stressed', 'smiling'):
        reply = get_fallback_expression_reply(emotion)
        assert reply == FALLBACK_EXPRESSION_REPLIES[emotion]
        assert len(reply.split()) <= 20


def test_feedback_service_submit_expression():
    generator = Mock()
    generator.generate_expression.return_value = "Shark wake up call!"
    speaker = Mock()
    speaker.speak.return_value = SpeechOutcome(True, True)

    service = FeedbackService(
        FeedbackConfig(enabled=True),
        cooldown_seconds=45.0,
        generator=generator,
        speaker=speaker
    )
    service.start()
    try:
        # Submit fatigue expression
        queued = service.submit_expression('fatigue')
        assert queued is True

        # Rapid second call should be dropped by cooldown
        queued_again = service.submit_expression('fatigue', cooldown=45.0)
        assert queued_again is False

        # Wait for worker thread to process job
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not speaker.speak.called:
            time.sleep(0.05)

        generator.generate_expression.assert_called_once_with('fatigue')
        speaker.speak.assert_called_once()
        assert speaker.speak.call_args[0][0] == "Shark wake up call!"
    finally:
        service.close()


def test_feedback_service_expression_offline_fallback():
    # Generator raises exception -> uses defined fallback audio lines
    generator = Mock()
    generator.generate_expression.side_effect = RuntimeError("API down")
    speaker = Mock()
    speaker.speak.return_value = SpeechOutcome(True, True)

    service = FeedbackService(
        FeedbackConfig(enabled=True),
        cooldown_seconds=0.0,
        generator=generator,
        speaker=speaker
    )
    service.start()
    try:
        service.submit_expression('stressed', cooldown=0.0)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not speaker.speak.called:
            time.sleep(0.05)

        speaker.speak.assert_called_once()
        assert speaker.speak.call_args[0][0] == FALLBACK_EXPRESSION_REPLIES['stressed']
    finally:
        service.close()
