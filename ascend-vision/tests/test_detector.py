from types import SimpleNamespace

import numpy as np
import pytest

from config import DetectorConfig
from detector import PhoneDetector


class Model:
    names = {0: 'person', 67: 'cell phone'}

    def __init__(self, rows):
        self.rows = np.array(rows, dtype=float).reshape(-1, 6)

    def predict(self, **kwargs):
        self.arguments = kwargs
        rows = self.rows
        return [SimpleNamespace(boxes=SimpleNamespace(
            xyxy=rows[:, :4], conf=rows[:, 4], cls=rows[:, 5]))]


def test_best_phone_only_and_local_inference_arguments():
    model = Model([[0, 0, 10, 10, .99, 0], [2, 3, 20, 30, .6, 67],
                   [4, 5, 40, 50, .9, 67]])
    detector = PhoneDetector(DetectorConfig(), model=model)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    box = detector.detect(frame)
    assert box.confidence == pytest.approx(.9)
    assert box.xyxy == (4., 5., 40., 50.)
    assert model.arguments['classes'] == [67]
    assert model.arguments['source'] is frame
    assert model.arguments['save'] is False
    assert model.arguments['device'] == 'cpu'


@pytest.mark.parametrize('rows', [[], [[0, 0, 10, 10, .49, 67]],
                                      [[0, 0, 10, 10, .99, 0]]])
def test_no_qualified_phone(rows):
    assert PhoneDetector(DetectorConfig(), model=Model(rows)).detect(
        np.zeros((20, 20, 3), dtype=np.uint8)) is None


def test_missing_phone_class_rejected():
    model = Model([])
    model.names = {0: 'person'}
    with pytest.raises(ValueError, match='cell phone'):
        PhoneDetector(DetectorConfig(), model=model)
