"""Single-owner camera thread with a bounded, latest-frame mailbox."""
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import sys
import threading
import time

import cv2
import numpy as np

from config import CameraConfig

LOG = logging.getLogger(__name__)


class CaptureError(RuntimeError):
    """The camera could not deliver a fresh frame."""


@dataclass(frozen=True)
class Frame:
    sequence: int
    captured_at: datetime
    monotonic_time: float
    image: np.ndarray


class CameraCapture:
    def __init__(self, config: CameraConfig, *, factory=None):
        self.config = config
        self._factory = factory or cv2.VideoCapture
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread = None
        self._latest = None
        self._error = None
        self._captured = 0

    @property
    def captured_count(self):
        with self._condition:
            return self._captured

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self._thread is not None or self._stop.is_set():
            raise RuntimeError('CameraCapture is single-use')
        self._thread = threading.Thread(target=self._worker, name='webcam', daemon=True)
        self._thread.start()
        return self

    def _worker(self):
        camera = None
        try:
            backend = {'auto': cv2.CAP_DSHOW if sys.platform == 'win32' else cv2.CAP_ANY,
                       'dshow': cv2.CAP_DSHOW,
                       'msmf': cv2.CAP_MSMF, 'v4l2': cv2.CAP_V4L2,
                       'avfoundation': cv2.CAP_AVFOUNDATION}[self.config.backend]
            camera = self._factory(self.config.index, backend)
            if not camera.isOpened():
                raise CaptureError(f'Cannot open camera {self.config.index}; check camera '
                                   'permissions, index, backend and other camera applications')
            for prop, value in [(cv2.CAP_PROP_FRAME_WIDTH, self.config.width),
                                (cv2.CAP_PROP_FRAME_HEIGHT, self.config.height),
                                (cv2.CAP_PROP_FPS, self.config.fps),
                                (cv2.CAP_PROP_BUFFERSIZE, 1)]:
                camera.set(prop, value)
            LOG.info('CAMERA index=%s negotiated_width=%.0f negotiated_height=%.0f '
                     'reported_fps=%.1f', self.config.index,
                     camera.get(cv2.CAP_PROP_FRAME_WIDTH),
                     camera.get(cv2.CAP_PROP_FRAME_HEIGHT), camera.get(cv2.CAP_PROP_FPS))
            while not self._stop.is_set():
                ok, image = camera.read()
                if self._stop.is_set():
                    break
                if not ok or image is None or image.size == 0:
                    raise CaptureError('Camera read failed; device disconnected or unavailable')
                # Copy prevents a backend from reusing a buffer being inferred on.
                image = image.copy()
                with self._condition:
                    self._captured += 1
                    self._latest = Frame(self._captured, datetime.now(timezone.utc),
                                         time.perf_counter(), image)
                    self._condition.notify_all()
        except Exception as exc:
            with self._condition:
                self._error = exc
                self._condition.notify_all()
        finally:
            try:
                if camera is not None:
                    camera.release()
            finally:
                self._stop.set()
                with self._condition:
                    self._condition.notify_all()

    def read(self, after_sequence: int, timeout: float) -> Frame:
        deadline = time.perf_counter() + timeout
        with self._condition:
            while True:
                if self._error is not None:
                    raise CaptureError(str(self._error)) from self._error
                if self._latest is not None and self._latest.sequence > after_sequence:
                    return self._latest
                if self._stop.is_set():
                    raise CaptureError('Camera stopped')
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise CaptureError(f'Camera read timed out after {timeout:.2f}s')
                self._condition.wait(remaining)

    def close(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is not None:
            self._thread.join(self.config.shutdown_timeout_seconds)
            if self._thread.is_alive():
                LOG.error('Camera driver did not stop within %.1fs; worker is a daemon',
                          self.config.shutdown_timeout_seconds)

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
