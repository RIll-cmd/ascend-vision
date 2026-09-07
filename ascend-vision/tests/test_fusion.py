from datetime import datetime, timedelta, timezone

import pytest

from config import DrowsinessConfig, HoldConfig, YawnConfig
from detector import PhoneBox
from drowsiness_detector import DrowsinessMachine
from safety_fusion import SafetyFusionManager
from state_machine import HoldMachine
from yawn_detector import YawnMachine

EPOCH = datetime(2026, 9, 6, tzinfo=timezone.utc)


def test_fusion_priority_drowsiness_over_phone_and_yawn():
    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=1, cooldown_seconds=10.0, calibration_frames=0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=1, cooldown_seconds=10.0, calibration_frames=0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m)

    # Synthetic face with closed eyes (drowsy) and wide open mouth (yawning)
    face = [(100.0, 100.0, 0.0)] * 478
    # Left eye: vert=0 => closed
    from drowsiness_detector import LEFT_EYE_LANDMARKS, RIGHT_EYE_LANDMARKS
    for idx in LEFT_EYE_LANDMARKS + RIGHT_EYE_LANDMARKS:
        face[idx] = (0.0, 0.0, 0.0)

    # Mouth: wide open => yawn
    from yawn_detector import MOUTH_LANDMARKS
    mouth_pts = [
        (0.0, 0.0, 0.0), (5.0, 10.0, 0.0), (10.0, 10.0, 0.0), (15.0, 10.0, 0.0),
        (20.0, 0.0, 0.0), (15.0, -10.0, 0.0), (10.0, -10.0, 0.0), (5.0, -10.0, 0.0)
    ]
    for idx, pt in zip(MOUTH_LANDMARKS, mouth_pts):
        face[idx] = pt

    # Phone held in hand
    phone = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)
    hands = [[(125.0, 150.0, 0.0)] * 21]

    # Update frame: all three trigger conditions simultaneously
    status = fusion.update(phone, hands, face, 0.0, EPOCH)

    # Verify priority: Drowsiness/Microsleep MUST take top priority over phone and yawn!
    assert status.primary_alert is not None
    assert status.primary_alert.event_type == 'drowsiness_microsleep'
    assert status.primary_alert.alert_allowed is True


def test_fusion_priority_yawn_over_phone():
    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    # Drowsiness requires 10 frames (so not triggered)
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=10, cooldown_seconds=10.0, calibration_frames=0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=1, cooldown_seconds=10.0, calibration_frames=0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m)

    face = [(100.0, 100.0, 0.0)] * 478
    from yawn_detector import MOUTH_LANDMARKS
    mouth_pts = [
        (0.0, 0.0, 0.0), (5.0, 10.0, 0.0), (10.0, 10.0, 0.0), (15.0, 10.0, 0.0),
        (20.0, 0.0, 0.0), (15.0, -10.0, 0.0), (10.0, -10.0, 0.0), (5.0, -10.0, 0.0)
    ]
    for idx, pt in zip(MOUTH_LANDMARKS, mouth_pts):
        face[idx] = pt

    phone = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)
    hands = [[(125.0, 150.0, 0.0)] * 21]

    status = fusion.update(phone, hands, face, 0.0, EPOCH)

    # Yawn takes priority over phone holding
    assert status.primary_alert is not None
    assert status.primary_alert.event_type == 'yawn'


def test_fusion_phone_alert_emitted_when_no_fatigue():
    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=10, cooldown_seconds=10.0, calibration_frames=0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=10, cooldown_seconds=10.0, calibration_frames=0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m)

    phone = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)
    hands = [[(125.0, 150.0, 0.0)] * 21]

    status = fusion.update(phone, hands, [], 0.0, EPOCH)

    assert status.primary_alert is not None
    assert status.primary_alert.event_type == 'phone_held'
    assert status.primary_alert.posture == 'active_hold'


def test_fusion_multi_event_lifecycle():
    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=10, cooldown_seconds=10.0, calibration_frames=0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=1, cooldown_seconds=10.0, calibration_frames=0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m)

    face = [(100.0, 100.0, 0.0)] * 478
    from yawn_detector import MOUTH_LANDMARKS
    mouth_pts = [
        (0.0, 0.0, 0.0), (5.0, 10.0, 0.0), (10.0, 10.0, 0.0), (15.0, 10.0, 0.0),
        (20.0, 0.0, 0.0), (15.0, -10.0, 0.0), (10.0, -10.0, 0.0), (5.0, -10.0, 0.0)
    ]
    for idx, pt in zip(MOUTH_LANDMARKS, mouth_pts):
        face[idx] = pt

    phone = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)
    hands = [[(125.0, 150.0, 0.0)] * 21]

    # Frame 1: both phone and yawn start
    status1 = fusion.update(phone, hands, face, 0.0, EPOCH)
    started_types = {e.event_type for e in status1.started_events}
    assert started_types == {'phone_held', 'yawn'}
    assert status1.primary_alert.event_type == 'yawn'  # yawn has priority for audio alert

    # Frame 2: both remain active
    status2 = fusion.update(phone, hands, face, 1.0, EPOCH + timedelta(seconds=1))
    active_types = {e.event_type for e in status2.active_events}
    assert active_types == {'phone_held', 'yawn'}

    # Frame 3: mouth closes, phone put down -> both end
    closed_mouth = [(0.0, 0.0, 0.0)] * 478
    status3 = fusion.update(None, [], closed_mouth, 2.0, EPOCH + timedelta(seconds=2))
    ended_types = {e.event_type for e in status3.ended_events}
    assert ended_types == {'phone_held', 'yawn'}


def test_fusion_fatigue_levels_and_scoring():
    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=2, cooldown_seconds=10.0, calibration_frames=0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=2, cooldown_seconds=10.0, calibration_frames=0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m)

    # 1. Normal face -> Alert
    open_face = [(100.0, 100.0, 0.0)] * 478
    from drowsiness_detector import LEFT_EYE_LANDMARKS, RIGHT_EYE_LANDMARKS
    for idx in LEFT_EYE_LANDMARKS + RIGHT_EYE_LANDMARKS:
        open_face[idx] = (10.0, 10.0, 0.0)
    # Give normal vertical eye span
    open_face[160] = (10.0, 5.0, 0.0); open_face[158] = (15.0, 5.0, 0.0)
    open_face[153] = (10.0, 15.0, 0.0); open_face[144] = (15.0, 15.0, 0.0)
    open_face[33] = (5.0, 10.0, 0.0); open_face[133] = (20.0, 10.0, 0.0)

    status = fusion.update(None, [], open_face, 0.0, EPOCH)
    assert status.fatigue_level == 'alert'
    assert status.fatigue_score == 0.0

    # 2. Closed eyes frame 1 -> Mild fatigue (closing)
    closed_face = [(100.0, 100.0, 0.0)] * 478
    for idx in LEFT_EYE_LANDMARKS + RIGHT_EYE_LANDMARKS:
        closed_face[idx] = (0.0, 0.0, 0.0)
    status_closing = fusion.update(None, [], closed_face, 0.1, EPOCH + timedelta(seconds=0.1))
    assert status_closing.fatigue_level in ('mild_fatigue', 'moderate_fatigue')
    assert 0.3 <= status_closing.fatigue_score < 1.0

    # 3. Closed eyes frame 2 -> Drowsy state -> Critical drowsy
    status_drowsy = fusion.update(None, [], closed_face, 0.2, EPOCH + timedelta(seconds=0.2))
    assert status_drowsy.fatigue_level == 'critical_drowsy'
    assert status_drowsy.fatigue_score == 1.0


def test_fusion_finish_shutdown():
    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=1, cooldown_seconds=10.0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=1, cooldown_seconds=10.0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m)
    res = fusion.finish('shutdown')
    assert 'hold' in res
    assert 'drowsiness' in res
    assert 'yawn' in res


def test_fusion_posture_alert_when_no_other_events():
    from config import PostureConfig
    from posture_detector import PostureMachine

    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=10, cooldown_seconds=10.0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=10, cooldown_seconds=10.0))
    posture_m = PostureMachine(PostureConfig(calibration_frames=5, consec_frames=1, cooldown_seconds=10.0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m, posture_m)

    # Synthetic upright face: forehead y=100, nose y=220, chin y=300
    from test_posture import posture_landmarks
    upright = posture_landmarks()
    slouched = posture_landmarks(y_offset=50.0)

    # 1. Calibrate posture (5 frames)
    for i in range(5):
        t = i * 0.1
        fusion.update(None, [], upright, t, EPOCH + timedelta(seconds=t))

    # 2. Slouched frame -> triggers slouch alert
    status = fusion.update(None, [], slouched, 0.5, EPOCH + timedelta(seconds=0.5))

    assert status.primary_alert is not None
    assert status.primary_alert.event_type == 'slouch'
    assert status.primary_alert.alert_allowed is True
    assert status.posture_status is not None
    assert status.posture_status.is_slouched is True
    assert {e.event_type for e in status.started_events} == {'slouch'}
    assert {e.event_type for e in status.active_events} == {'slouch'}

    # 3. User sits upright -> posture event ends
    status_restore = fusion.update(None, [], upright, 1.0, EPOCH + timedelta(seconds=1.0))
    assert {e.event_type for e in status_restore.ended_events} == {'slouch'}
    assert status_restore.ended_events[0].reason == 'posture_restored'


def test_fusion_priority_phone_over_slouch():
    from config import PostureConfig
    from posture_detector import PostureMachine
    from test_posture import posture_landmarks

    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=10, cooldown_seconds=10.0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=10, cooldown_seconds=10.0))
    posture_m = PostureMachine(PostureConfig(calibration_frames=5, consec_frames=1, cooldown_seconds=10.0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m, posture_m)

    upright = posture_landmarks()
    slouched = posture_landmarks(y_offset=50.0)

    for i in range(5):
        fusion.update(None, [], upright, i * 0.1, EPOCH + timedelta(seconds=i * 0.1))

    # Simultaneously trigger phone held and slouch
    phone = PhoneBox((100.0, 100.0, 150.0, 200.0), 0.9)
    hands = [[(125.0, 150.0, 0.0)] * 21]

    status = fusion.update(phone, hands, slouched, 0.5, EPOCH + timedelta(seconds=0.5))

    # Both events start and are active
    started_types = {e.event_type for e in status.started_events}
    assert started_types == {'phone_held', 'slouch'}

    # Priority: Phone distraction outranks slouch
    assert status.primary_alert is not None
    assert status.primary_alert.event_type == 'phone_held'


def test_fusion_finish_with_posture():
    from config import PostureConfig
    from posture_detector import PostureMachine

    hold_m = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=10.0))
    drowsy_m = DrowsinessMachine(DrowsinessConfig(ear_threshold=0.22, closed_consec_frames=1, cooldown_seconds=10.0))
    yawn_m = YawnMachine(YawnConfig(mar_threshold=0.65, yawn_consec_frames=1, cooldown_seconds=10.0))
    posture_m = PostureMachine(PostureConfig(calibration_frames=5, consec_frames=1, cooldown_seconds=10.0))

    fusion = SafetyFusionManager(hold_m, drowsy_m, yawn_m, posture_m)
    res = fusion.finish('shutdown')
    assert 'hold' in res
    assert 'drowsiness' in res
    assert 'yawn' in res
    assert 'posture' in res


