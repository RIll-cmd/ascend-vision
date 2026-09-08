"""MediaPipe Tasks hand tracking on the same local frames used for phone inference."""
import math
from pathlib import Path
import tempfile
import urllib.request

import cv2
import numpy as np

from config import HandConfig


def download_hand_model(path: Path):
    path = Path(path)
    if path.is_file():
        return path
    if path.name != 'hand_landmarker.task':
        raise ValueError('Hand model setup requires hand_landmarker.task; supply other models locally')
    url = ('https://storage.googleapis.com/mediapipe-models/hand_landmarker/'
           'hand_landmarker/float16/1/hand_landmarker.task')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as out:
                temporary = Path(out.name)
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
        if temporary.stat().st_size < 1024 * 1024:
            raise RuntimeError('Downloaded hand model is unexpectedly small')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


class HandTracker:
    def __init__(self, config: HandConfig, *, landmarker=None, image_factory=None):
        self._last_ms = -1
        self._closed = False
        if landmarker is None:
            if not config.model.is_file():
                raise ValueError(f'Hand model not found: {config.model}. Run main.py --download-model first')
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
            options = vision.HandLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(config.model)),
                running_mode=vision.RunningMode.VIDEO, num_hands=config.num_hands,
                min_hand_detection_confidence=config.detection_confidence,
                min_hand_presence_confidence=config.presence_confidence,
                min_tracking_confidence=config.tracking_confidence)
            landmarker = vision.HandLandmarker.create_from_options(options)
            image_factory = lambda rgb: mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        if image_factory is None:
            raise ValueError('A supplied landmarker also requires an image_factory')
        self._landmarker = landmarker
        self._image_factory = image_factory
        self.last_handedness = ()

    def detect(self, frame, timestamp_seconds):
        if self._closed:
            raise RuntimeError('HandTracker is closed')
        if not math.isfinite(timestamp_seconds) or timestamp_seconds < 0:
            raise ValueError('Hand timestamp must be finite and nonnegative')
        # Millisecond rounding must not repeat a timestamp in the Tasks VIDEO API.
        timestamp_ms = max(self._last_ms + 1, int(timestamp_seconds * 1000))
        self._last_ms = timestamp_ms
        rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = self._landmarker.detect_for_video(self._image_factory(rgb), timestamp_ms)
        self.last_handedness = tuple(
            categories[0].category_name if categories else None
            for categories in getattr(result, 'handedness', ())
        )
        height, width = frame.shape[:2]
        # z uses MediaPipe's width-relative scale; geometry intentionally uses x/y only.
        return [[(lm.x * width, lm.y * height, lm.z * width) for lm in hand]
                for hand in result.hand_landmarks if len(hand) == 21]

    def close(self):
        if not self._closed:
            self._closed = True
            self._landmarker.close()

    def warmup(self, width, height):
        self.detect(np.zeros((height, width, 3), dtype=np.uint8), 0.)
