from datetime import datetime, timezone

import pytest

from assistant.eye_calibration import EyeCalibrationStore, EyeCalibrator, threshold_from
from config import DrowsinessConfig
from drowsiness_detector import DrowsinessMachine


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(tmp_path):
    machine = DrowsinessMachine(DrowsinessConfig())
    store = EyeCalibrationStore(tmp_path / 'eye_calibration.json')
    said, clock = [], Clock()
    calibrator = EyeCalibrator(store, machine, announce=said.append, clock=clock)
    return calibrator, machine, store, said, clock


def run(calibrator, clock, seconds, ear, fps=10):
    for _ in range(int(seconds * fps)):
        clock.t += 1 / fps
        calibrator.observe(ear)


def test_line_sits_between_open_and_closed_and_ignores_blinks():
    open_samples = [0.30] * 40 + [0.05] * 4            # four blinks while "open"
    threshold, open_level, closed_level = threshold_from(open_samples, [0.10] * 20)
    assert open_level == pytest.approx(0.30) and closed_level == pytest.approx(0.10)
    assert threshold == pytest.approx(0.10 + 0.4 * 0.20)
    no_closed, _, _ = threshold_from([0.30] * 40, [])
    assert no_closed == pytest.approx(0.195)


def test_guided_calibration_measures_saves_and_applies(tmp_path):
    calibrator, machine, store, said, clock = make(tmp_path)
    assert calibrator.intercept('can you calibrate my eyes?').startswith("Let's calibrate your eyes (without glasses).")
    assert machine.suspended
    run(calibrator, clock, 2, 0.05)                      # still listening: these frames are ignored
    run(calibrator, clock, 6.2, 0.31)
    assert said[-1].startswith('Good. Now gently close your eyes')
    run(calibrator, clock, 6.2, 0.09)
    assert said[-1].startswith('Open your eyes. Done: your eyes are calibrated without glasses and saved.')
    assert not machine.suspended and not calibrator.active
    assert machine.effective_threshold == pytest.approx(0.09 + 0.4 * 0.22)
    assert store.profile('no_glasses')['open'] == pytest.approx(0.31)


def test_no_face_during_calibration_fails_politely(tmp_path):
    calibrator, machine, _store, said, clock = make(tmp_path)
    calibrator.start()
    run(calibrator, clock, 9, None)
    assert "couldn't see your face" in said[-1] and not machine.suspended and not calibrator.active


def test_glasses_profiles_are_separate_and_switchable(tmp_path):
    calibrator, machine, store, said, clock = make(tmp_path)
    assert "don't have a calibration with glasses" in calibrator.intercept('glasses on')
    calibrator.start()
    run(calibrator, clock, 8.2, 0.26)
    run(calibrator, clock, 6.2, 0.12)
    glasses_line = machine.effective_threshold
    assert store.profile('glasses') is not None and store.profile('no_glasses') is None
    calibrator.intercept('glasses off')
    assert machine.effective_threshold == DrowsinessConfig().ear_threshold   # back to automatic
    assert calibrator.intercept("I'm wearing my glasses") == 'Okay, using your eye calibration with glasses.'
    assert machine.effective_threshold == pytest.approx(glasses_line)
    restarted, machine2, _, _, _ = make(tmp_path)
    assert restarted.apply_saved() and machine2.effective_threshold == pytest.approx(glasses_line)


def test_unrelated_chat_is_not_taken():
    calibrator = EyeCalibrator(EyeCalibrationStore('unused.json'), DrowsinessMachine(DrowsinessConfig()),
                               announce=lambda _t: None)
    assert calibrator.intercept('what is the weather?') is None


def face(ear):
    """Minimal 478-point face whose six eye points produce roughly the given eye openness."""
    from drowsiness_detector import LEFT_EYE_LANDMARKS, RIGHT_EYE_LANDMARKS
    points = [(0.0, 0.0, 0.0)] * 478
    for eye, x0 in ((LEFT_EYE_LANDMARKS, 100.0), (RIGHT_EYE_LANDMARKS, 200.0)):
        p1, p2, p3, p4, p5, p6 = eye
        width = 40.0
        height = ear * width        # EAR = (|p2-p6| + |p3-p5|) / (2 |p1-p4|)
        coords = {p1: (x0, 50), p4: (x0 + width, 50), p2: (x0 + 13, 50 - height / 2), p6: (x0 + 13, 50 + height / 2),
                  p3: (x0 + 27, 50 - height / 2), p5: (x0 + 27, 50 + height / 2)}
        for index, (x, y) in coords.items():
            points[index] = (x, y, 0.0)
    return points


def test_automatic_startup_calibration_is_not_dragged_down_by_blinks():
    machine = DrowsinessMachine(DrowsinessConfig(calibration_frames=30))
    now = 0.0
    for index in range(30):
        now += 0.1
        machine.update(face(0.08 if index % 5 == 0 else 0.30), now, datetime.now(timezone.utc))
    assert machine.calibrated
    assert machine.baseline_ear == pytest.approx(0.30, abs=0.01)     # a mean would be about 0.256


def test_suspended_machine_never_reports_drowsiness():
    machine = DrowsinessMachine(DrowsinessConfig(calibration_frames=0, closed_consec_frames=3))
    machine.suspended = True
    now = 0.0
    for _ in range(20):
        now += 0.1
        update = machine.update(face(0.05), now, datetime.now(timezone.utc))
    assert update.state == 'calibrating' and update.started is None and machine.active is None
