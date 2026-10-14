"""Assistant mode: Vision without the camera or any camera AI.

``run()`` takes its camera and models as parameters, so assistant mode passes these stand-ins.
torch, Ultralytics and MediaPipe are only imported inside the real models' constructors,
so none of them is ever loaded. Voice, speech, chat, coaches and the agent room keep running.
"""
from datetime import datetime, timezone
import time

import numpy as np

from capture import Frame

BLANK = np.zeros((1, 1, 3), dtype=np.uint8)


class IdleCapture:
    """Hands the main loop a blank frame a few times a second, so its timers keep ticking."""

    def __init__(self, tick_seconds: float = 0.25):
        self.tick_seconds = tick_seconds
        self.captured_count = 0

    def start(self):
        return self

    def read(self, after_sequence, timeout):
        time.sleep(max(0.0, min(self.tick_seconds, timeout)))
        self.captured_count += 1
        return Frame(after_sequence + 1, datetime.now(timezone.utc), time.monotonic(), BLANK)

    def close(self):
        pass


class NoCameraModel:
    """Stands in for the phone detector and the hand and face trackers: it never sees anything."""

    # run() turns face-based features (away mode) off when the face tracker is not available.
    available = False
    last_handedness = ()
    last_confidences = ()

    def detect(self, *_args):
        return None

    def landmarks(self, *_args):
        return []

    def warmup(self, *_args):
        pass

    def close(self):
        pass


class NoTracker(NoCameraModel):
    """Hand and face trackers return landmark lists, never None."""

    def detect(self, *_args):
        return []
