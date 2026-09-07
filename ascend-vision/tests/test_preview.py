import numpy as np
from datetime import datetime, timezone
from unittest.mock import Mock

from capture import Frame
from config import Config
from detector import PhoneBox
from main import run


def test_preview_quit_cleans_up_and_does_not_mutate_capture_frame(monkeypatch):
    frame = np.zeros((80, 80, 3), dtype=np.uint8)
    capture = Mock(captured_count=1)
    capture.read.return_value = Frame(1, datetime.now(timezone.utc), 0, frame)
    detector = Mock()
    detector.detect.return_value = PhoneBox((10., 10., 40., 40.), .9)
    hands = Mock()
    hands.detect.return_value = []
    shown = []
    closed = []
    monkeypatch.setattr('main.cv2.namedWindow', lambda *a: None)
    monkeypatch.setattr('main.cv2.imshow', lambda name, image: shown.append(image))
    monkeypatch.setattr('main.cv2.waitKey', lambda *a: ord('q'))
    monkeypatch.setattr('main.cv2.destroyAllWindows', lambda: closed.append(True))
    run(Config(), detector=detector, capture=capture, hand_tracker=hands)
    assert len(shown) == 1
    assert shown[0].any()
    assert not frame.any()
    assert closed == [True]
    capture.close.assert_called_once()
    hands.close.assert_called_once()
