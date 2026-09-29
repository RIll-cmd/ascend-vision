from integrations.desk_presence import PresenceDebouncer


def test_healthy_camera_absence_stays_unknown_until_ten_second_dwell():
    tracker = PresenceDebouncer(absence_seconds=10, return_seconds=3)

    assert tracker.update(False, 0.0, source_available=True) == "unknown"
    for tick in range(1, 20):
        assert tracker.update(False, tick * 0.5, source_available=True) == "unknown"
    assert tracker.update(False, 10.0, source_available=True) == "away"


def test_return_requires_three_seconds_of_stable_presence():
    tracker = PresenceDebouncer(absence_seconds=10, return_seconds=3)
    tracker.update(False, 0.0, source_available=True)
    for tick in range(1, 21):
        tracker.update(False, tick * 0.5, source_available=True)

    assert tracker.update(True, 10.5, source_available=True) == "away"
    for tick in range(1, 6):
        assert tracker.update(True, 10.5 + tick * 0.5, source_available=True) == "away"
    assert tracker.update(True, 13.5, source_available=True) == "present"


def test_camera_loss_immediately_invalidates_presence_and_resets_dwell():
    tracker = PresenceDebouncer(absence_seconds=10, return_seconds=3)
    tracker.update(True, 0.0, source_available=True)
    tracker.update(True, 3.0, source_available=True)

    assert tracker.update(False, 4.0, source_available=False) == "unknown"
    assert tracker.update(False, 20.0, source_available=True) == "unknown"
    for tick in range(1, 20):
        assert tracker.update(False, 20.0 + tick * 0.5, source_available=True) == "unknown"
    assert tracker.update(False, 30.0, source_available=True) == "away"


def test_timing_discontinuity_resets_candidate_instead_of_confirming_absence():
    tracker = PresenceDebouncer(absence_seconds=10, return_seconds=3)
    tracker.update(False, 0.0, source_available=True)

    assert tracker.update(False, 20.0, source_available=True) == "unknown"
    for tick in range(1, 20):
        assert tracker.update(False, 20.0 + tick * 0.5, source_available=True) == "unknown"
    assert tracker.update(False, 30.0, source_available=True) == "away"
