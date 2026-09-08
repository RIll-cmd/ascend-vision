from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest

from config import HandConfig, load_config
from hands import HandTracker


def test_rgb_conversion_pixel_coordinates_and_timestamp_rounding():
    model = Mock()
    model.detect_for_video.return_value = SimpleNamespace(hand_landmarks=[
        [SimpleNamespace(x=.25, y=.5, z=-.1)] * 21])
    tracker = HandTracker(HandConfig(), landmarker=model, image_factory=lambda rgb: rgb)
    bgr = np.full((100, 200, 3), [10, 20, 30], dtype=np.uint8)
    for t in [1., 1.0001, 1.0002]:
        points = tracker.detect(bgr, t)
    assert points[0][8] == (50., 50., -20.)
    calls = model.detect_for_video.call_args_list
    assert [c.args[1] for c in calls] == [1000, 1001, 1002]
    assert calls[0].args[0][0, 0].tolist() == [30, 20, 10]
    assert bgr[0, 0].tolist() == [10, 20, 30]
    tracker.close()
    tracker.close()
    model.close.assert_called_once()
    with pytest.raises(RuntimeError, match='closed'):
        tracker.detect(bgr, 2.)


def test_detect_exposes_handedness_alongside_existing_landmarks():
    model = Mock()
    model.detect_for_video.return_value = SimpleNamespace(
        hand_landmarks=[[SimpleNamespace(x=.1, y=.2, z=.0)] * 21],
        handedness=[[SimpleNamespace(category_name='Left')]],
    )
    tracker = HandTracker(HandConfig(), landmarker=model, image_factory=lambda rgb: rgb)

    assert tracker.detect(np.zeros((2, 2, 3), dtype=np.uint8), 1.0)
    assert tracker.last_handedness == ('Left',)
    tracker.close()


def test_missing_hand_model_has_setup_message(tmp_path):
    with pytest.raises(ValueError, match='download-model'):
        HandTracker(HandConfig(model=tmp_path / 'missing.task'))


@pytest.mark.parametrize('content', ['hands:\n  num_hands: 3',
    'hands:\n  tracking_confidence: .nan', 'hands:\n  model: https://example.org/file.task',
    'hold:\n  distance_metric: invalid', 'hold:\n  max_observation_gap_seconds: 0'])
def test_invalid_phase2_settings(tmp_path, content):
    path = tmp_path / 'config.yaml'
    path.write_text(content)
    with pytest.raises(ValueError):
        load_config(path)


def test_hand_path_resolves_from_config(tmp_path):
    path = tmp_path / 'config.yaml'
    path.write_text('hands:\n  model: assets/hand_landmarker.task')
    assert load_config(path).hands.model == tmp_path / 'assets/hand_landmarker.task'
