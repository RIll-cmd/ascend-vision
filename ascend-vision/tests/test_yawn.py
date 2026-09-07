from datetime import datetime, timedelta, timezone

import pytest

from config import YawnConfig
from yawn_detector import (
    MOUTH_LANDMARKS,
    YawnMachine,
    calculate_face_mar,
    mouth_aspect_ratio,
)

EPOCH = datetime(2026, 9, 6, tzinfo=timezone.utc)


def synthetic_mouth(vert=6.0, horiz=20.0):
    """Generate 8 mouth points for MAR testing.
    m1 (left corner): (0, 0)
    m2 (top left): (horiz * 0.25, vert / 2)
    m3 (top center): (horiz * 0.5, vert / 2)
    m4 (top right): (horiz * 0.75, vert / 2)
    m5 (right corner): (horiz, 0)
    m6 (bottom right): (horiz * 0.75, -vert / 2)
    m7 (bottom center): (horiz * 0.5, -vert / 2)
    m8 (bottom left): (horiz * 0.25, -vert / 2)

    Formula:
      v1 = ||m2 - m8|| = vert
      v2 = ||m3 - m7|| = vert
      v3 = ||m4 - m6|| = vert
      h = ||m1 - m5|| = horiz
      MAR = (v1 + v2 + v3) / (2 * h) = 3 * vert / (2 * horiz)
    """
    return [
        (0.0, 0.0, 0.0),                  # m1
        (horiz * 0.25, vert / 2.0, 0.0),  # m2
        (horiz * 0.50, vert / 2.0, 0.0),  # m3
        (horiz * 0.75, vert / 2.0, 0.0),  # m4
        (horiz, 0.0, 0.0),                # m5
        (horiz * 0.75, -vert / 2.0, 0.0), # m6
        (horiz * 0.50, -vert / 2.0, 0.0), # m7
        (horiz * 0.25, -vert / 2.0, 0.0), # m8
    ]


def synthetic_face_with_mouth(vert=6.0, horiz=20.0):
    """Generate 478 face landmarks containing mouth points."""
    face = [(100.0, 100.0, 0.0)] * 478
    mouth_pts = synthetic_mouth(vert=vert, horiz=horiz)
    for idx, pt in zip(MOUTH_LANDMARKS, mouth_pts):
        face[idx] = pt
    return face


def test_mouth_aspect_ratio_mathematics():
    # vert=20, horiz=20 => MAR = 3 * 20 / (2 * 20) = 1.5
    mouth = synthetic_mouth(vert=20.0, horiz=20.0)
    assert mouth_aspect_ratio(mouth) == pytest.approx(1.5)

    # Closed mouth: vert=0 => MAR = 0.0
    closed_mouth = synthetic_mouth(vert=0.0, horiz=20.0)
    assert mouth_aspect_ratio(closed_mouth) == pytest.approx(0.0)

    # Invalid points length
    with pytest.raises(ValueError, match='Expected 8'):
        mouth_aspect_ratio([(0.0, 0.0, 0.0)] * 7)

    # Zero width corner-to-corner safely returns 0.0
    zero_width = [(0.0, 0.0, 0.0)] * 8
    assert mouth_aspect_ratio(zero_width) == 0.0


def test_calculate_face_mar():
    # Yawn: vert=10.0, horiz=20.0 => MAR = 3 * 10 / (2 * 20) = 0.75
    yawn_face = synthetic_face_with_mouth(vert=10.0, horiz=20.0)
    assert calculate_face_mar(yawn_face) == pytest.approx(0.75)

    # Speaking: vert=4.0, horiz=20.0 => MAR = 3 * 4 / (2 * 20) = 0.30
    speaking_face = synthetic_face_with_mouth(vert=4.0, horiz=20.0)
    assert calculate_face_mar(speaking_face) == pytest.approx(0.30)

    # Empty or truncated landmarks returns 0.0 safely
    assert calculate_face_mar([]) == 0.0
    assert calculate_face_mar([(0.0, 0.0, 0.0)] * 200) == 0.0


def test_speech_does_not_trigger_yawn():
    config = YawnConfig(mar_threshold=0.65, yawn_consec_frames=10, cooldown_seconds=10.0, calibration_frames=0)
    machine = YawnMachine(config)

    speaking_face = synthetic_face_with_mouth(vert=4.0, horiz=20.0)  # MAR ~ 0.30
    closed_face = synthetic_face_with_mouth(vert=1.0, horiz=20.0)    # MAR ~ 0.075

    # 50 frames of speech
    for frame in range(50):
        face = speaking_face if frame % 2 == 0 else closed_face
        t = frame * 0.05
        res = machine.update(face, t, EPOCH + timedelta(seconds=t))
        assert res.state == 'closed'
        assert res.consecutive_frames == 0
        assert res.started is None
        assert res.active is None
        assert res.yawn_count == 0


def test_brief_wide_mouth_does_not_trigger_yawn():
    config = YawnConfig(mar_threshold=0.65, yawn_consec_frames=10, cooldown_seconds=10.0, calibration_frames=0)
    machine = YawnMachine(config)

    yawn_face = synthetic_face_with_mouth(vert=10.0, horiz=20.0)   # MAR = 0.75
    closed_face = synthetic_face_with_mouth(vert=1.0, horiz=20.0)

    # Open wide for 5 frames (< 10 threshold)
    for frame in range(5):
        t = frame * 0.05
        res = machine.update(yawn_face, t, EPOCH + timedelta(seconds=t))
        assert res.state == 'opening'
        assert res.consecutive_frames == frame + 1
        assert res.started is None

    # Close mouth -> resets without triggering yawn
    t = 0.30
    res = machine.update(closed_face, t, EPOCH + timedelta(seconds=t))
    assert res.state == 'closed'
    assert res.consecutive_frames == 0
    assert res.started is None
    assert res.yawn_count == 0


def test_sustained_yawn_triggers_event_and_tracks_duration():
    config = YawnConfig(mar_threshold=0.65, yawn_consec_frames=5, cooldown_seconds=10.0, calibration_frames=0)
    machine = YawnMachine(config)

    yawn_face = synthetic_face_with_mouth(vert=10.0, horiz=20.0)   # MAR = 0.75
    closed_face = synthetic_face_with_mouth(vert=1.0, horiz=20.0)

    # Frames 0-3: Opening
    for frame in range(4):
        t = frame * 0.1
        res = machine.update(yawn_face, t, EPOCH + timedelta(seconds=t))
        assert res.state == 'opening'
        assert res.started is None

    # Frame 4: Hits 5 consecutive frames -> Yawn started!
    res = machine.update(yawn_face, 0.4, EPOCH + timedelta(seconds=0.4))
    assert res.state == 'yawning'
    assert res.consecutive_frames == 5
    assert res.started is not None
    assert res.started.id == 1
    assert res.started.alert_allowed is True
    assert res.cooldown_remaining == pytest.approx(10.0)

    # Frame 5: Sustained yawn -> active duration updates
    res = machine.update(yawn_face, 0.5, EPOCH + timedelta(seconds=0.5))
    assert res.state == 'yawning'
    assert res.started is None
    assert res.active is not None
    assert res.active.duration_seconds == pytest.approx(0.1)

    # Frame 6: Mouth closes -> Yawn ended
    res = machine.update(closed_face, 0.6, EPOCH + timedelta(seconds=0.6))
    assert res.state == 'closed'
    assert res.active is None
    assert res.ended is not None
    assert res.ended.duration_seconds == pytest.approx(0.2)
    assert res.ended.end_reason == 'closed'
    assert res.yawn_count == 1


def test_yawn_cooldown_suppression():
    config = YawnConfig(mar_threshold=0.65, yawn_consec_frames=2, cooldown_seconds=5.0, calibration_frames=0)
    machine = YawnMachine(config)

    yawn_face = synthetic_face_with_mouth(vert=10.0, horiz=20.0)
    closed_face = synthetic_face_with_mouth(vert=1.0, horiz=20.0)

    # Yawn 1
    machine.update(yawn_face, 0.0, EPOCH)
    y1 = machine.update(yawn_face, 0.1, EPOCH + timedelta(seconds=0.1))
    assert y1.started.alert_allowed is True

    # Close mouth
    machine.update(closed_face, 0.2, EPOCH + timedelta(seconds=0.2))

    # Yawn 2 at t=1.0 (within 5.0s cooldown)
    machine.update(yawn_face, 1.0, EPOCH + timedelta(seconds=1.0))
    y2 = machine.update(yawn_face, 1.1, EPOCH + timedelta(seconds=1.1))
    assert y2.started is not None
    assert y2.started.id == 2
    assert y2.started.alert_allowed is False
    assert y2.cooldown_remaining == pytest.approx(4.0)

    # Close mouth and advance beyond cooldown
    machine.update(closed_face, 1.2, EPOCH + timedelta(seconds=1.2))
    machine.update(yawn_face, 6.0, EPOCH + timedelta(seconds=6.0))
    y3 = machine.update(yawn_face, 6.1, EPOCH + timedelta(seconds=6.1))
    assert y3.started is not None
    assert y3.started.id == 3
    assert y3.started.alert_allowed is True


def test_yawn_face_lost():
    config = YawnConfig(mar_threshold=0.65, yawn_consec_frames=2, cooldown_seconds=5.0, calibration_frames=0)
    machine = YawnMachine(config)

    yawn_face = synthetic_face_with_mouth(vert=10.0, horiz=20.0)

    machine.update(yawn_face, 0.0, EPOCH)
    machine.update(yawn_face, 0.1, EPOCH + timedelta(seconds=0.1))
    assert machine.active is not None

    # Face disappears
    res = machine.update([], 0.2, EPOCH + timedelta(seconds=0.2))
    assert res.state == 'closed'
    assert res.active is None
    assert res.ended is not None
    assert res.ended.end_reason == 'face_lost'


def test_yawn_machine_adaptive_calibration():
    # 5 frames calibration
    config = YawnConfig(
        mar_threshold=0.65,
        yawn_consec_frames=2,
        calibration_frames=5,
        adaptive_threshold_ratio=1.75
    )
    machine = YawnMachine(config)
    normal_face = synthetic_face_with_mouth(vert=2.0, horiz=20.0)  # MAR = 0.15

    assert machine.calibrated is False
    for i in range(4):
        res = machine.update(normal_face, i * 0.1, EPOCH + timedelta(seconds=i * 0.1))
        assert res.state == 'calibrating'
        assert res.calibrated is False
        assert res.calibration_progress == pytest.approx((i + 1) / 5.0)

    # 5th frame completes calibration
    res5 = machine.update(normal_face, 0.4, EPOCH + timedelta(seconds=0.4))
    assert res5.calibrated is True
    assert machine.baseline_mar == pytest.approx(0.15)
    # Floor is config.mar_threshold = 0.65
    assert machine.effective_threshold == pytest.approx(0.65)
    assert res5.state == 'closed'
