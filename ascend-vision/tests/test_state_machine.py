from datetime import datetime, timedelta, timezone

import pytest

from config import HoldConfig
from detector import PhoneBox
from state_machine import HoldMachine, proximity_distance

BOX = PhoneBox((100., 100., 200., 200.), .9)
EPOCH = datetime(2026, 9, 6, tzinfo=timezone.utc)


def hand(point=(150., 150., 0.), index=8):
    points = [(500., 500., 0.)] * 21
    points[index] = point
    return points


def update(machine, t, hands=None, box=BOX):
    return machine.update(box, [hand()] if hands is None else hands,
                          t, EPOCH + timedelta(seconds=t))


def test_phone_on_desk_without_near_wrist_or_tip_never_confirms():
    machine = HoldMachine(HoldConfig(threshold_frames=3))
    for t in range(20):
        result = update(machine, t, [hand(index=9)])
        assert result.started is None
        assert result.state == 'idle'
    assert update(machine, 20, []).state == 'idle'
    assert update(machine, 21, box=None).state == 'idle'


def test_only_consecutive_frames_confirm_and_duration_starts_at_confirmation():
    machine = HoldMachine(HoldConfig(threshold_frames=3))
    assert update(machine, 0).state == 'candidate'
    assert update(machine, .1).started is None
    update(machine, .2, [])
    update(machine, .3)
    update(machine, .4)
    confirmed = update(machine, .5)
    assert confirmed.started.duration_seconds == 0
    assert confirmed.started.started_at == EPOCH + timedelta(seconds=.5)
    for t in [.6, .7, .8]:
        result = update(machine, t)
        assert result.started is None
    assert result.active.duration_seconds == pytest.approx(.3)
    released = update(machine, .9, [])
    assert released.ended.duration_seconds == pytest.approx(.4)
    assert released.ended.end_reason == 'released'
    assert released.state == 'idle'
    assert update(machine, 1, []).ended is None


def test_cooldown_never_loses_pickups_and_never_repeats_long_hold():
    machine = HoldMachine(HoldConfig(threshold_frames=1, cooldown_seconds=2))
    assert update(machine, 0).started.alert_allowed
    assert update(machine, .1, []).ended
    second = update(machine, .2).started
    assert second.id == 2 and not second.alert_allowed
    for t in [.5, 1., 1.5, 2., 2.5]:
        assert update(machine, t).started is None
    update(machine, 2.6, [])
    assert update(machine, 2.7).started.alert_allowed


def test_stall_breaks_candidate_and_closes_active_at_last_observation():
    machine = HoldMachine(HoldConfig(threshold_frames=2, max_observation_gap_seconds=.5))
    update(machine, 0)
    assert update(machine, 1).state == 'candidate'
    update(machine, 1.1)
    result = update(machine, 2)
    assert result.ended.duration_seconds == 0
    assert result.ended.end_reason == 'observation_gap'
    assert result.state == 'candidate'


def test_shutdown_finalizes_at_last_observation_once():
    machine = HoldMachine(HoldConfig(threshold_frames=1))
    update(machine, 0)
    update(machine, .5)
    assert machine.finish('shutdown').duration_seconds == .5
    assert machine.finish('shutdown') is None


def test_strict_boundary_and_configurable_geometry():
    assert proximity_distance(BOX, [hand((210., 150., 0.))], 'center') == 60
    assert proximity_distance(BOX, [hand((210., 150., 0.))], 'box') == 10
    machine = HoldMachine(HoldConfig(threshold_frames=1, proximity_px=60))
    assert update(machine, 0, [hand((210., 150., 0.))]).started is None


@pytest.mark.parametrize('timestamp', [0, -.1, float('nan'), float('inf')])
def test_repeated_reversed_or_invalid_timestamps_rejected(timestamp):
    machine = HoldMachine(HoldConfig())
    update(machine, 0)
    with pytest.raises(ValueError):
        machine.update(BOX, [hand()], timestamp, EPOCH)


def test_two_hands_and_nonfinite_landmarks():
    machine = HoldMachine(HoldConfig(threshold_frames=1))
    assert update(machine, 0, [hand((float('nan'), 150., 0.))]).started is None
    assert update(machine, .1, [hand((600., 600., 0.)), hand()]).started
