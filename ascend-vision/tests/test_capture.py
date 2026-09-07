import threading
import time

import numpy as np
import pytest

from capture import CameraCapture, CaptureError
from config import CameraConfig


class Camera:
    def __init__(self, opened=True, fail_after=None):
        self.opened = opened
        self.fail_after = fail_after
        self.count = 0
        self.released = False
        self.read_thread = None

    def isOpened(self):
        return self.opened

    def set(self, *_):
        return True

    def get(self, *_):
        return 0

    def read(self):
        self.read_thread = threading.get_ident()
        time.sleep(.005)
        self.count += 1
        if self.fail_after is not None and self.count > self.fail_after:
            return False, None
        return True, np.full((4, 4, 3), self.count % 255, dtype=np.uint8)

    def release(self):
        self.released = True


def test_capture_uses_worker_and_discards_old_frames():
    camera = Camera()
    with CameraCapture(CameraConfig(), factory=lambda *_: camera) as capture:
        first = capture.read(0, timeout=1)
        time.sleep(.06)
        newest = capture.read(first.sequence, timeout=1)
        assert newest.sequence > first.sequence + 1
        assert newest.captured_at > first.captured_at
        assert camera.read_thread != threading.get_ident()
    assert camera.released
    assert not capture.is_alive


def test_open_failure_releases_device():
    camera = Camera(opened=False)
    with pytest.raises(CaptureError, match='open'):
        with CameraCapture(CameraConfig(), factory=lambda *_: camera) as capture:
            capture.read(0, timeout=1)
    assert camera.released


def test_read_failure_propagates_and_cleans_up():
    camera = Camera(fail_after=0)
    with pytest.raises(CaptureError, match='read'):
        with CameraCapture(CameraConfig(), factory=lambda *_: camera) as capture:
            capture.read(0, timeout=1)
    assert camera.released


def test_timeout_does_not_hang():
    gate = threading.Event()
    camera = Camera()
    original = camera.read
    camera.read = lambda: (gate.wait(1), original())[1]
    capture = CameraCapture(CameraConfig(), factory=lambda *_: camera)
    try:
        capture.start()
        with pytest.raises(CaptureError, match='timed out'):
            capture.read(0, timeout=.02)
    finally:
        gate.set()
        capture.close()
    assert camera.released
