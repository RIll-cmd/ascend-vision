import io

import pytest

from detector import download_model, PhoneDetector
from config import DetectorConfig
from hands import download_hand_model


def test_atomic_download_and_existing_file_no_network(tmp_path, monkeypatch):
    calls = []
    def response(url, timeout):
        calls.append(url)
        return io.BytesIO(b'x' * (1024 * 1024))
    monkeypatch.setattr('detector.urllib.request.urlopen', response)
    path = tmp_path / 'models' / 'yolo26n.pt'
    assert download_model(path) == path
    assert path.stat().st_size == 1024 * 1024
    assert download_model(path) == path
    assert len(calls) == 1
    assert not list(path.parent.glob('*.part'))


def test_invalid_download_does_not_leave_corrupt_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setattr('detector.urllib.request.urlopen', lambda *a, **k: io.BytesIO(b'bad'))
    path = tmp_path / 'yolo26n.pt'
    with pytest.raises(RuntimeError, match='small'):
        download_model(path)
    assert not path.exists()
    assert not list(tmp_path.glob('*.part'))


def test_missing_model_fails_before_loading_library(tmp_path):
    with pytest.raises(ValueError, match='download-model'):
        PhoneDetector(DetectorConfig(model=tmp_path / 'yolo26n.pt'))


def test_hand_download_is_atomic_and_idempotent(tmp_path, monkeypatch):
    calls = []
    def response(url, timeout):
        calls.append(url)
        return io.BytesIO(b'x' * (1024 * 1024))
    monkeypatch.setattr('hands.urllib.request.urlopen', response)
    path = tmp_path / 'hand_landmarker.task'
    assert download_hand_model(path) == path
    assert download_hand_model(path) == path
    assert len(calls) == 1
    assert not list(tmp_path.glob('*.part'))


def test_truncated_hand_download_is_removed(tmp_path, monkeypatch):
    monkeypatch.setattr('hands.urllib.request.urlopen', lambda *a, **k: io.BytesIO(b'bad'))
    path = tmp_path / 'hand_landmarker.task'
    with pytest.raises(RuntimeError, match='small'):
        download_hand_model(path)
    assert not path.exists()
    assert not list(tmp_path.glob('*.part'))
