"""MediaPipe Tasks face mesh tracking on the same local frames."""
import math
from pathlib import Path
import tempfile
import urllib.request

import cv2
import numpy as np

from config import FaceConfig


def download_face_model(path: Path):
    path = Path(path)
    if path.is_file():
        return path
    if path.name != 'face_landmarker.task':
        raise ValueError('Face model setup requires face_landmarker.task; supply other models locally')
    url = ('https://storage.googleapis.com/mediapipe-models/face_landmarker/'
           'face_landmarker/float16/1/face_landmarker.task')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as out:
                temporary = Path(out.name)
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
        if temporary.stat().st_size < 1024 * 1024:
            raise RuntimeError('Downloaded face model is unexpectedly small')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


class FaceMeshTracker:
    def __init__(self, config: FaceConfig, *, landmarker=None, image_factory=None):
        self._last_ms = -1
        self._closed = False
        if landmarker is None:
            if not config.model.is_file():
                raise ValueError(f'Face model not found: {config.model}. Run download_face_model first')
            import mediapipe as mp
            from mediapipe.tasks import python
            from mediapipe.tasks.python import vision
            options = vision.FaceLandmarkerOptions(
                base_options=python.BaseOptions(model_asset_path=str(config.model)),
                running_mode=vision.RunningMode.VIDEO,
                num_faces=config.num_faces,
                min_face_detection_confidence=config.detection_confidence,
                min_face_presence_confidence=config.presence_confidence,
                min_tracking_confidence=config.tracking_confidence,
                output_face_blendshapes=True,
                output_facial_transformation_matrixes=False)
            landmarker = vision.FaceLandmarker.create_from_options(options)
            image_factory = lambda rgb: mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        if image_factory is None:
            raise ValueError('A supplied landmarker also requires an image_factory')
        self._landmarker = landmarker
        self._image_factory = image_factory

    def detect_with_blendshapes(
        self, frame: np.ndarray, timestamp_seconds: float
    ) -> tuple[list[list[tuple[float, float, float]]], list[dict[str, float]]]:
        if self._closed:
            raise RuntimeError('FaceMeshTracker is closed')
        if not math.isfinite(timestamp_seconds) or timestamp_seconds < 0:
            raise ValueError('Face timestamp must be finite and nonnegative')
        timestamp_ms = max(self._last_ms + 1, int(timestamp_seconds * 1000))
        self._last_ms = timestamp_ms
        rgb = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        result = self._landmarker.detect_for_video(self._image_factory(rgb), timestamp_ms)
        height, width = frame.shape[:2]
        face_landmarks = getattr(result, 'face_landmarks', None) or []
        landmarks = [[(lm.x * width, lm.y * height, lm.z * width) for lm in face]
                     for face in face_landmarks if len(face) >= 468]
        blendshapes: list[dict[str, float]] = []
        raw_blendshapes = getattr(result, 'face_blendshapes', None)
        if raw_blendshapes:
            for face_bs in raw_blendshapes:
                bs_map = {
                    cat.category_name: float(cat.score)
                    for cat in face_bs
                    if hasattr(cat, 'category_name')
                }
                blendshapes.append(bs_map)
        return landmarks, blendshapes

    def detect(self, frame: np.ndarray, timestamp_seconds: float) -> list[list[tuple[float, float, float]]]:
        landmarks, _ = self.detect_with_blendshapes(frame, timestamp_seconds)
        return landmarks

    def close(self):
        if not self._closed:
            self._closed = True
            self._landmarker.close()

    def warmup(self, width: int, height: int):
        self.detect(np.zeros((height, width, 3), dtype=np.uint8), 0.)
