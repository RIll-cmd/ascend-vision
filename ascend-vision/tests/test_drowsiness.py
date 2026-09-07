from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from config import Config, DrowsinessConfig, FaceConfig, load_config
from drowsiness_detector import (
    LEFT_EYE_LANDMARKS,
    RIGHT_EYE_LANDMARKS,
    DrowsinessMachine,
    calculate_face_ear,
    eye_aspect_ratio,
)
from face_mesh import FaceMeshTracker

EPOCH = datetime(2026, 9, 6, tzinfo=timezone.utc)


def synthetic_eye(p1=(0., 0.), p2=(10., 15.), p3=(20., 15.), p4=(30., 0.), p5=(20., -15.), p6=(10., -15.)):
    """Generate 6 points for eye landmark tests.
    With default coordinates:
      ||p2 - p6|| = 30
      ||p3 - p5|| = 30
      ||p1 - p4|| = 30
      EAR = (30 + 30) / (2 * 30) = 1.0
    """
    return [
        (float(p1[0]), float(p1[1]), 0.),
        (float(p2[0]), float(p2[1]), 0.),
        (float(p3[0]), float(p3[1]), 0.),
        (float(p4[0]), float(p4[1]), 0.),
        (float(p5[0]), float(p5[1]), 0.),
        (float(p6[0]), float(p6[1]), 0.),
    ]


def synthetic_face(open_eyes: bool = True):
    """Generate 478 face landmarks with specified eye openness."""
    face = [(100., 100., 0.)] * 478
    # Left eye: indices (33, 160, 158, 133, 153, 144)
    # Right eye: indices (362, 385, 387, 263, 373, 380)
    # Open eye: vertical distance ~ 6.0, horizontal ~ 20.0 => EAR ~ (6+6)/(2*20) = 0.30
    # Closed eye: vertical distance ~ 1.0, horizontal ~ 20.0 => EAR ~ (1+1)/(2*20) = 0.05
    vert = 6.0 if open_eyes else 1.0
    horiz = 20.0

    eye_pts = [
        (0.0, 0.0, 0.0),                     # p1: outer corner
        (horiz * 0.33, vert / 2.0, 0.0),     # p2: top-outer
        (horiz * 0.66, vert / 2.0, 0.0),     # p3: top-inner
        (horiz, 0.0, 0.0),                   # p4: inner corner
        (horiz * 0.66, -vert / 2.0, 0.0),    # p5: bottom-inner
        (horiz * 0.33, -vert / 2.0, 0.0),    # p6: bottom-outer
    ]

    for idx, pt in zip(LEFT_EYE_LANDMARKS, eye_pts):
        face[idx] = pt
    for idx, pt in zip(RIGHT_EYE_LANDMARKS, eye_pts):
        face[idx] = pt

    return face


def test_eye_aspect_ratio_mathematics():
    eye = synthetic_eye()
    assert eye_aspect_ratio(eye) == pytest.approx(1.0)

    # Closed eye: vertical gap is 0
    closed_eye = synthetic_eye(p2=(10., 0.), p3=(20., 0.), p5=(20., 0.), p6=(10., 0.))
    assert eye_aspect_ratio(closed_eye) == pytest.approx(0.0)

    # Invalid point length raises ValueError
    with pytest.raises(ValueError, match='Expected 6'):
        eye_aspect_ratio([(0., 0.)] * 5)

    # Non-finite coordinates return 0.0 safely
    nan_eye = synthetic_eye(p1=(float('nan'), 0.))
    assert eye_aspect_ratio(nan_eye) == 0.0


def test_calculate_face_ear():
    open_face = synthetic_face(open_eyes=True)
    l_ear, r_ear, avg_ear = calculate_face_ear(open_face)
    assert avg_ear == pytest.approx(0.30)
    assert l_ear == pytest.approx(0.30)
    assert r_ear == pytest.approx(0.30)

    closed_face = synthetic_face(open_eyes=False)
    l_ear, r_ear, avg_ear = calculate_face_ear(closed_face)
    assert avg_ear == pytest.approx(0.05)

    # Insufficient landmarks returns zeros safely
    assert calculate_face_ear([]) == (0.0, 0.0, 0.0)
    assert calculate_face_ear([(0., 0., 0.)] * 100) == (0.0, 0.0, 0.0)


def test_drowsiness_machine_awake_and_blink_counter():
    config = DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=5, cooldown_seconds=10.0, calibration_frames=0)
    machine = DrowsinessMachine(config)

    open_face = synthetic_face(open_eyes=True)
    closed_face = synthetic_face(open_eyes=False)

    # Frame 0: Awake
    res = machine.update(open_face, 0.0, EPOCH)
    assert res.state == 'awake'
    assert res.consecutive_frames == 0
    assert res.blink_count == 0
    assert res.started is None
    assert res.active is None

    # Frames 1-3: Eye closure (3 frames < threshold 5)
    for t in [0.1, 0.2, 0.3]:
        res = machine.update(closed_face, t, EPOCH + timedelta(seconds=t))
        assert res.state == 'closing'
        assert res.consecutive_frames == int(round(t * 10))
        assert res.started is None

    # Frame 4: Eye reopens -> Blink recorded!
    res = machine.update(open_face, 0.4, EPOCH + timedelta(seconds=0.4))
    assert res.state == 'awake'
    assert res.consecutive_frames == 0
    assert res.blink_count == 1
    assert res.started is None
    assert res.ended is None


def test_drowsiness_machine_sustained_closure_triggers_drowsy():
    config = DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=4, cooldown_seconds=5.0, calibration_frames=0)
    machine = DrowsinessMachine(config)

    open_face = synthetic_face(open_eyes=True)
    closed_face = synthetic_face(open_eyes=False)

    # Frames 0-2: Closing
    machine.update(closed_face, 0.0, EPOCH)
    machine.update(closed_face, 0.1, EPOCH + timedelta(seconds=0.1))
    machine.update(closed_face, 0.2, EPOCH + timedelta(seconds=0.2))

    # Frame 3: Hits 4 consecutive frames -> Drowsy state!
    drowsy_res = machine.update(closed_face, 0.3, EPOCH + timedelta(seconds=0.3))
    assert drowsy_res.state == 'drowsy'
    assert drowsy_res.consecutive_frames == 4
    assert drowsy_res.started is not None
    assert drowsy_res.started.id == 1
    assert drowsy_res.started.alert_allowed is True
    assert drowsy_res.cooldown_remaining == pytest.approx(5.0)

    # Frame 4: Remains closed -> Active duration updates
    active_res = machine.update(closed_face, 0.4, EPOCH + timedelta(seconds=0.4))
    assert active_res.state == 'drowsy'
    assert active_res.started is None
    assert active_res.active is not None
    assert active_res.active.duration_seconds == pytest.approx(0.1)

    # Frame 5: Reopens -> Event ended
    reopened = machine.update(open_face, 0.5, EPOCH + timedelta(seconds=0.5))
    assert reopened.state == 'awake'
    assert reopened.ended is not None
    assert reopened.ended.end_reason == 'reopened'
    assert reopened.ended.duration_seconds == pytest.approx(0.2)
    assert reopened.active is None


def test_drowsiness_machine_cooldown_suppression():
    config = DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=2, cooldown_seconds=5.0, calibration_frames=0)
    machine = DrowsinessMachine(config)

    open_face = synthetic_face(open_eyes=True)
    closed_face = synthetic_face(open_eyes=False)

    # Episode 1
    machine.update(closed_face, 0.0, EPOCH)
    ep1 = machine.update(closed_face, 0.1, EPOCH + timedelta(seconds=0.1))
    assert ep1.started.alert_allowed is True

    # Reopen
    machine.update(open_face, 0.2, EPOCH + timedelta(seconds=0.2))

    # Episode 2 at t=1.0 (within 5.0s cooldown)
    machine.update(closed_face, 1.0, EPOCH + timedelta(seconds=1.0))
    ep2 = machine.update(closed_face, 1.1, EPOCH + timedelta(seconds=1.1))
    assert ep2.started is not None
    assert ep2.started.id == 2
    assert ep2.started.alert_allowed is False
    assert ep2.cooldown_remaining == pytest.approx(4.0)

    # Reopen and advance past cooldown (t=6.0)
    machine.update(open_face, 1.2, EPOCH + timedelta(seconds=1.2))
    machine.update(closed_face, 6.0, EPOCH + timedelta(seconds=6.0))
    ep3 = machine.update(closed_face, 6.1, EPOCH + timedelta(seconds=6.1))
    assert ep3.started is not None
    assert ep3.started.id == 3
    assert ep3.started.alert_allowed is True


def test_drowsiness_machine_face_lost_behavior():
    config = DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=2, cooldown_seconds=5.0, calibration_frames=0)
    machine = DrowsinessMachine(config)
    closed_face = synthetic_face(open_eyes=False)

    machine.update(closed_face, 0.0, EPOCH)
    machine.update(closed_face, 0.1, EPOCH + timedelta(seconds=0.1))
    assert machine.active is not None

    # Face lost (empty landmarks list)
    lost_res = machine.update([], 0.2, EPOCH + timedelta(seconds=0.2))
    assert lost_res.state == 'awake'
    assert lost_res.ended is not None
    assert lost_res.ended.end_reason == 'face_lost'
    assert lost_res.active is None


def test_face_mesh_tracker_mock():
    mock_model = Mock()
    mock_model.detect_for_video.return_value = SimpleNamespace(face_landmarks=[
        [SimpleNamespace(x=0.5, y=0.5, z=-0.05)] * 478
    ])
    tracker = FaceMeshTracker(FaceConfig(), landmarker=mock_model, image_factory=lambda rgb: rgb)
    bgr = np.full((100, 200, 3), [10, 20, 30], dtype=np.uint8)

    pts = tracker.detect(bgr, 1.0)
    assert len(pts) == 1
    assert len(pts[0]) == 478
    assert pts[0][0] == (100.0, 50.0, -10.0)

    tracker.close()
    mock_model.close.assert_called_once()
    with pytest.raises(RuntimeError, match='closed'):
        tracker.detect(bgr, 2.0)


def test_drowsiness_machine_adaptive_calibration():
    # 5 frames calibration
    config = DrowsinessConfig(
        ear_threshold=0.22,
        closed_consec_frames=2,
        calibration_frames=5,
        adaptive_threshold_ratio=0.70
    )
    machine = DrowsinessMachine(config)
    open_face = synthetic_face(open_eyes=True)  # EAR = 0.30

    assert machine.calibrated is False
    for i in range(4):
        res = machine.update(open_face, i * 0.1, EPOCH + timedelta(seconds=i * 0.1))
        assert res.state == 'calibrating'
        assert res.calibrated is False
        assert res.calibration_progress == pytest.approx((i + 1) / 5.0)

    # 5th frame completes calibration
    res5 = machine.update(open_face, 0.4, EPOCH + timedelta(seconds=0.4))
    assert res5.calibrated is True
    assert machine.baseline_ear == pytest.approx(0.30)
    assert machine.effective_threshold == pytest.approx(0.30 * 0.70)  # 0.21
    assert res5.state == 'awake'
