"""Local YOLO26 inference, returning only the best cell-phone box."""
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import urllib.request

import numpy as np

from config import DetectorConfig


@dataclass(frozen=True)
class PhoneBox:
    xyxy: tuple[float, float, float, float]
    confidence: float


def download_model(path: Path):
    """Explicit, atomic download of the official nano checkpoint, no camera access."""
    path = Path(path)
    if path.is_file():
        return path
    if path.name != 'yolo26n.pt':
        raise ValueError('Automatic setup supports yolo26n.pt only; supply other weights locally')
    path.parent.mkdir(parents=True, exist_ok=True)
    url = 'https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt'
    temporary = None
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=path.parent, suffix='.part', delete=False) as out:
                temporary = Path(out.name)
                while chunk := response.read(1024 * 1024):
                    out.write(chunk)
        if temporary.stat().st_size < 1024 * 1024:
            raise RuntimeError('Downloaded model is unexpectedly small')
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


class PhoneDetector:
    def __init__(self, config: DetectorConfig, *, model=None):
        self.config = config
        if model is None:
            if not config.model.is_file():
                raise ValueError(f'Model not found: {config.model}. Run main.py --download-model first')
            # Configure privacy before importing the model library.
            os.environ['YOLO_OFFLINE'] = 'true'
            os.environ['YOLO_CONFIG_DIR'] = str(Path(__file__).resolve().parent / '.ultralytics')
            os.environ['YOLO_VERBOSE'] = 'false'
            import torch
            from ultralytics import YOLO, settings
            settings.update({'sync': False})
            torch.set_num_threads(config.cpu_threads)
            model = YOLO(str(config.model), task='detect')
        self.model = model
        names = model.names
        items = names.items() if isinstance(names, dict) else enumerate(names)
        self.phone_class = next((int(index) for index, name in items if name == 'cell phone'), None)
        if self.phone_class is None:
            raise ValueError('Model does not contain the COCO cell phone class')

    def detect(self, frame: np.ndarray) -> PhoneBox | None:
        results = self.model.predict(source=frame, conf=self.config.confidence,
                                     classes=[self.phone_class], imgsz=self.config.image_size,
                                     device=self.config.device, verbose=False,
                                     save=False, save_txt=False, save_crop=False,
                                     show=False, stream=False)
        if not results or results[0].boxes is None:
            return None
        boxes = results[0].boxes
        def array(value):
            return value.detach().cpu().numpy() if hasattr(value, 'detach') else np.asarray(value)
        xyxy, confidences, classes = map(array, (boxes.xyxy, boxes.conf, boxes.cls))
        valid = np.flatnonzero((classes == self.phone_class)
                               & (confidences >= self.config.confidence)
                               & np.isfinite(confidences)
                               & np.isfinite(xyxy).all(axis=1))
        if not len(valid):
            return None
        best = valid[np.argmax(confidences[valid])]
        return PhoneBox(tuple(float(v) for v in xyxy[best]), float(confidences[best]))

    def warmup(self, width: int, height: int):
        self.detect(np.zeros((height, width, 3), dtype=np.uint8))
