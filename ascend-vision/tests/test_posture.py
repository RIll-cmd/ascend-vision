from datetime import datetime, timedelta, timezone

import pytest

from config import HoldConfig
from detector import PhoneBox
from posture import TrajectorySmoother, classify_phone_posture, get_face_bbox
from state_machine import HoldMachine

EPOCH = datetime(2026, 9, 6, tzinfo=timezone.utc)


def hand_at(x=150.0, y=150.0, wrist_idx=0):
    points = [(500.0, 500.0, 0.0)] * 21
    points[wrist_idx] = (x, y, 0.0)
    return points


def face_bbox_landmarks(x1=200.0, y1=100.0, x2=300.0, y2=250.0):
    """Generate 478 landmarks that span [x1, x2] and [y1, y2]."""
    pts = [( (x1 + x2)/2.0, (y1 + y2)/2.0, 0.0 )] * 478
    pts[0] = (x1, y1, 0.0)
    pts[1] = (x2, y2, 0.0)
    return pts


def test_get_face_bbox():
    face = face_bbox_landmarks(50.0, 60.0, 150.0, 180.0)
    bbox = get_face_bbox(face)
    assert bbox == (50.0, 60.0, 150.0, 180.0)
    assert get_face_bbox([]) is None
    assert get_face_bbox([(0., 0., 0.)] * 5) is None


def test_classify_phone_posture_call_to_head():
    # Face at [200, 100] to [300, 250] (center: 250, 175)
    face = face_bbox_landmarks(200.0, 100.0, 300.0, 250.0)
    # Phone held beside head at x=[160, 210], y=[140, 220] (center: 185, 180)
    phone_call = PhoneBox((160.0, 140.0, 210.0, 220.0), 0.9)
    hands = [hand_at(185.0, 180.0)]

    posture = classify_phone_posture(phone_call, hands, face, call_proximity_px=120.0)
    assert posture == 'call'


def test_classify_phone_posture_texting_lap():
    # Face at [200, 100] to [300, 250]
    face = face_bbox_landmarks(200.0, 100.0, 300.0, 250.0)
    # Phone held low down in lap at y=[350, 450] (below jaw 250 + 40)
    phone_texting = PhoneBox((220.0, 350.0, 280.0, 450.0), 0.9)
    hands = [hand_at(250.0, 400.0)]

    posture = classify_phone_posture(phone_texting, hands, face)
    assert posture == 'texting'


def test_classify_phone_posture_active_hold_when_no_face():
    phone = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)
    hands = [hand_at(125.0, 150.0)]
    posture = classify_phone_posture(phone, hands, None)
    assert posture == 'active_hold'
    assert classify_phone_posture(None, hands, None) == 'none'
    assert classify_phone_posture(phone, [], None) == 'none'


def test_trajectory_smoother_transient_reach_veto():
    smoother = TrajectorySmoother(max_frames=5)
    phone_box = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)

    # Hand rapidly moves across the screen past a stationary phone
    # dt = 0.05s, hand moves from x=0 to x=100 (v = 2000 px/s)
    # phone remains stationary at (125, 150)
    t0 = 0.0
    hands0 = [hand_at(0.0, 150.0)]
    score0 = smoother.update(t0, phone_box, hands0)
    assert score0 == 1.0  # first frame neutral

    t1 = 0.05
    hands1 = [hand_at(120.0, 150.0)]
    score1 = smoother.update(t1, phone_box, hands1)
    assert score1 < 0.2  # rapid sweeping hand detected!


def test_trajectory_smoother_tandem_motion_stable():
    smoother = TrajectorySmoother(max_frames=5)

    # Phone and hand move together in tandem
    t0 = 0.0
    phone0 = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)
    hands0 = [hand_at(125.0, 150.0)]
    smoother.update(t0, phone0, hands0)

    t1 = 0.05
    phone1 = PhoneBox((110.0, 110.0, 160.0, 210.0), 0.9)
    hands1 = [hand_at(135.0, 160.0)]
    score1 = smoother.update(t1, phone1, hands1)
    assert score1 > 0.8  # tandem hold is highly stable


def test_hold_machine_with_posture_classification():
    config = HoldConfig(threshold_frames=2, proximity_px=60.0)
    machine = HoldMachine(config)

    face = face_bbox_landmarks(200.0, 100.0, 300.0, 250.0)
    phone = PhoneBox((160.0, 140.0, 210.0, 220.0), 0.9)  # beside head
    hands = [hand_at(185.0, 180.0)]

    u0 = machine.update(phone, hands, 0.0, EPOCH, face_landmarks=face)
    assert u0.posture == 'call'

    u1 = machine.update(phone, hands, 0.1, EPOCH + timedelta(seconds=0.1), face_landmarks=face)
    assert u1.started is not None
    assert u1.started.posture == 'call'
    assert u1.state == 'holding'


from config import PostureConfig
from posture_detector import (
    PostureCalibrator,
    PostureMachine,
    calculate_face_pitch,
    calculate_normalized_head_drop,
)


def posture_landmarks(y_offset: float = 0.0, dz_chin: float = 0.0) -> list[tuple[float, float, float]]:
    """Generate 478 face landmarks with customizable vertical offset and chin depth."""
    pts = [(200.0, 200.0, 0.0)] * 478
    # Forehead at y=100, chin at y=300, nose at y=220
    pts[10] = (200.0, 100.0 + y_offset, 0.0)
    pts[1] = (200.0, 220.0 + y_offset, 0.0)
    pts[152] = (200.0, 300.0 + y_offset, dz_chin)
    return pts


def test_calculate_face_pitch():
    # Upright face with no chin depth -> 0 deg pitch
    upright = posture_landmarks(y_offset=0.0, dz_chin=0.0)
    assert abs(calculate_face_pitch(upright)) < 1e-4

    # Neck flexion forward tilt -> chin dz = 50.0, dy = 200.0 -> atan2(50, 200) = ~14.03 deg
    tilted = posture_landmarks(y_offset=0.0, dz_chin=50.0)
    pitch = calculate_face_pitch(tilted)
    assert 13.5 < pitch < 14.5

    # Edge cases
    assert calculate_face_pitch([]) == 0.0
    assert calculate_face_pitch([(0.0, 0.0, 0.0)] * 10) == 0.0


def test_calculate_normalized_head_drop():
    # Upright face: forehead y=100, chin y=300 -> face_h = 200. nose y=220 -> drop = 220/200 = 1.10
    upright = posture_landmarks(y_offset=0.0)
    drop_upright = calculate_normalized_head_drop(upright)
    assert abs(drop_upright - 1.10) < 1e-4

    # Slouched face: dropped by 50px -> nose y=270, face_h = 200 -> drop = 270/200 = 1.35
    slouched = posture_landmarks(y_offset=50.0)
    drop_slouched = calculate_normalized_head_drop(slouched)
    assert abs(drop_slouched - 1.35) < 1e-4
    assert (drop_slouched - drop_upright) > 0.20

    # Edge cases
    assert calculate_normalized_head_drop([]) == 0.0
    assert calculate_normalized_head_drop([(0.0, 0.0, 0.0)] * 5) == 0.0


def test_posture_calibrator():
    calibrator = PostureCalibrator(target_frames=10)
    assert not calibrator.calibrated
    assert calibrator.progress == 0.0

    upright = posture_landmarks()
    # Feed 9 frames
    for _ in range(9):
        assert not calibrator.update(upright)
    assert calibrator.progress == 0.9
    assert not calibrator.calibrated

    # Feed 10th frame -> calibrated!
    assert calibrator.update(upright)
    assert calibrator.calibrated
    assert calibrator.progress == 1.0
    assert abs(calibrator.baseline_drop - 1.10) < 1e-4
    assert abs(calibrator.baseline_pitch - 0.0) < 1e-4

    # Subsequent update returns True immediately
    assert calibrator.update(upright)

    # Recalibration
    calibrator.recalibrate()
    assert not calibrator.calibrated
    assert calibrator.progress == 0.0

    # Manual baseline injection
    calibrator.set_baseline(pitch=2.5, drop=1.15)
    assert calibrator.calibrated
    assert calibrator.baseline_pitch == 2.5
    assert calibrator.baseline_drop == 1.15


def test_posture_machine_upright_no_false_positives():
    config = PostureConfig(calibration_frames=5, consec_frames=3, cooldown_seconds=10.0)
    machine = PostureMachine(config)

    upright = posture_landmarks()
    # Calibrate in 5 frames
    for i in range(5):
        t = i * 0.1
        u = machine.update(upright, t, EPOCH + timedelta(seconds=t))
        assert u.state == 'calibrating'

    # Now calibrated, feed upright frames continuously
    for i in range(5, 15):
        t = i * 0.1
        u = machine.update(upright, t, EPOCH + timedelta(seconds=t))
        assert u.state == 'upright'
        assert u.is_slouched is False
        assert u.consecutive_frames == 0
        assert u.active is None
        assert u.started is None
        assert u.ended is None


def test_posture_machine_sustained_slouch_and_recovery():
    config = PostureConfig(calibration_frames=5, consec_frames=3, cooldown_seconds=10.0)
    machine = PostureMachine(config)

    upright = posture_landmarks(y_offset=0.0)
    slouched = posture_landmarks(y_offset=50.0)  # delta drop = 0.25 > 0.15 threshold

    # 1. Calibrate (5 frames)
    for i in range(5):
        t = i * 0.1
        machine.update(upright, t, EPOCH + timedelta(seconds=t))

    # 2. Slouching starts (frames 1 & 2 debouncing)
    u1 = machine.update(slouched, 0.5, EPOCH + timedelta(seconds=0.5))
    assert u1.state == 'slouching'
    assert u1.consecutive_frames == 1
    assert u1.is_slouched is True
    assert u1.active is None
    assert u1.started is None

    u2 = machine.update(slouched, 0.6, EPOCH + timedelta(seconds=0.6))
    assert u2.state == 'slouching'
    assert u2.consecutive_frames == 2
    assert u2.active is None

    # Frame 3: reaches consec_frames=3 -> confirms slouch!
    u3 = machine.update(slouched, 0.7, EPOCH + timedelta(seconds=0.7))
    assert u3.state == 'slouched'
    assert u3.consecutive_frames == 3
    assert u3.active is not None
    assert u3.started is not None
    assert u3.started.alert_allowed is True
    assert u3.started.slouch_score > 1.0

    # Frame 4: stays slouched, updates duration
    u4 = machine.update(slouched, 1.0, EPOCH + timedelta(seconds=1.0))
    assert u4.state == 'slouched'
    assert u4.started is None
    assert u4.active is not None
    assert abs(u4.active.duration_seconds - 0.3) < 1e-4

    # 3. Recovery to upright posture
    u5 = machine.update(upright, 1.2, EPOCH + timedelta(seconds=1.2))
    assert u5.state == 'upright'
    assert u5.active is None
    assert u5.ended is not None
    assert u5.ended.end_reason == 'posture_restored'
    assert u5.ended.reason == 'posture_restored'
    assert abs(u5.ended.duration_seconds - 0.5) < 1e-4


def test_posture_machine_pitch_neck_flexion():
    config = PostureConfig(calibration_frames=5, consec_frames=2, pitch_threshold_deg=10.0)
    machine = PostureMachine(config)

    upright = posture_landmarks(dz_chin=0.0)
    flexion = posture_landmarks(dz_chin=50.0)  # ~14.03 deg > 10.0 deg

    for i in range(5):
        machine.update(upright, i * 0.1, EPOCH + timedelta(seconds=i * 0.1))

    # Frame 1 flexion
    u1 = machine.update(flexion, 0.5, EPOCH + timedelta(seconds=0.5))
    assert u1.is_slouched is True
    assert u1.state == 'slouching'

    # Frame 2 flexion
    u2 = machine.update(flexion, 0.6, EPOCH + timedelta(seconds=0.6))
    assert u2.state == 'slouched'
    assert u2.started is not None


def test_posture_machine_cooldown_suppression():
    config = PostureConfig(calibration_frames=5, consec_frames=2, cooldown_seconds=15.0)
    machine = PostureMachine(config)

    upright = posture_landmarks()
    slouched = posture_landmarks(y_offset=50.0)

    # Calibrate (5 frames)
    for i in range(5):
        machine.update(upright, i * 0.1, EPOCH + timedelta(seconds=i * 0.1))

    # Event 1 triggered at t=0.6
    machine.update(slouched, 0.5, EPOCH + timedelta(seconds=0.5))
    u_evt1 = machine.update(slouched, 0.6, EPOCH + timedelta(seconds=0.6))
    assert u_evt1.started is not None
    assert u_evt1.started.alert_allowed is True

    # User sits upright at t=0.8 -> ends event 1
    machine.update(upright, 0.8, EPOCH + timedelta(seconds=0.8))

    # Event 2 triggered at t=5.0 (only 4.4s since t=0.6 alert < 15.0s cooldown)
    machine.update(slouched, 4.9, EPOCH + timedelta(seconds=4.9))
    u_evt2 = machine.update(slouched, 5.0, EPOCH + timedelta(seconds=5.0))
    assert u_evt2.started is not None
    assert u_evt2.started.alert_allowed is False  # Cooldown active!

    # Sits upright again at t=6.0
    machine.update(upright, 6.0, EPOCH + timedelta(seconds=6.0))

    # Event 3 triggered at t=20.0 (19.4s since t=0.6 alert > 15.0s cooldown)
    machine.update(slouched, 19.9, EPOCH + timedelta(seconds=19.9))
    u_evt3 = machine.update(slouched, 20.0, EPOCH + timedelta(seconds=20.0))
    assert u_evt3.started is not None
    assert u_evt3.started.alert_allowed is True  # Cooldown expired!


def test_posture_machine_jitter_tolerance():
    # Test that transient upright frames break slouch accumulation
    config = PostureConfig(calibration_frames=5, consec_frames=5)
    machine = PostureMachine(config)

    upright = posture_landmarks()
    slouched = posture_landmarks(y_offset=50.0)

    # Calibrate
    for i in range(5):
        machine.update(upright, i * 0.1, EPOCH + timedelta(seconds=i * 0.1))

    # Slouch for 3 frames (out of 5 needed)
    machine.update(slouched, 0.5, EPOCH + timedelta(seconds=0.5))
    machine.update(slouched, 0.6, EPOCH + timedelta(seconds=0.6))
    u3 = machine.update(slouched, 0.7, EPOCH + timedelta(seconds=0.7))
    assert u3.consecutive_frames == 3
    assert u3.state == 'slouching'

    # 1 transient upright frame resets debounce
    u_break = machine.update(upright, 0.8, EPOCH + timedelta(seconds=0.8))
    assert u_break.consecutive_frames == 0
    assert u_break.state == 'upright'
    assert u_break.active is None

    # Needs 5 fresh frames to confirm
    for i in range(4):
        t = 0.9 + i * 0.1
        u = machine.update(slouched, t, EPOCH + timedelta(seconds=t))
        assert u.state == 'slouching'
        assert u.active is None

    u_final = machine.update(slouched, 1.3, EPOCH + timedelta(seconds=1.3))
    assert u_final.state == 'slouched'
    assert u_final.started is not None


def test_posture_machine_face_lost_and_shutdown():
    config = PostureConfig(calibration_frames=5, consec_frames=1)
    machine = PostureMachine(config)

    upright = posture_landmarks()
    slouched = posture_landmarks(y_offset=50.0)

    # Calibrate & trigger slouch
    for i in range(5):
        machine.update(upright, i * 0.1, EPOCH + timedelta(seconds=i * 0.1))

    u_slouch = machine.update(slouched, 0.5, EPOCH + timedelta(seconds=0.5))
    assert u_slouch.active is not None

    # Face lost ends slouch
    u_lost = machine.update([], 0.8, EPOCH + timedelta(seconds=0.8))
    assert u_lost.active is None
    assert u_lost.ended is not None
    assert u_lost.ended.end_reason == 'face_lost'

    # Retrigger and test finish shutdown
    machine.update(slouched, 1.0, EPOCH + timedelta(seconds=1.0))
    assert machine.active is not None
    ended_shutdown = machine.finish('shutdown')
    assert ended_shutdown is not None
    assert ended_shutdown.end_reason == 'shutdown'
    assert machine.active is None

