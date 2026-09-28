import json

import numpy as np
import pytest

from assistant.context_runtime import DeskRegion, ContextRuntime
from integrations.context_collector import CompanionContextCollector
from integrations.desk_presence import (
    DeskRegionStore,
    face_center_in_region,
    is_camera_frame_usable,
)


def test_desk_region_uses_face_center_and_normalized_bounds():
    region = DeskRegion(left=0.2, top=0.1, right=0.8, bottom=0.9)

    assert face_center_in_region([(40, 50, 0), (60, 70, 0)], region, 100, 100)
    assert not face_center_in_region([(0, 50, 0), (10, 70, 0)], region, 100, 100)


def test_desk_region_rejects_empty_or_out_of_bounds_rectangles():
    with pytest.raises(ValueError):
        DeskRegion(left=0.5, top=0.2, right=0.5, bottom=0.8)
    with pytest.raises(ValueError):
        DeskRegion(left=-0.1, top=0.2, right=0.5, bottom=0.8)
    with pytest.raises(ValueError):
        DeskRegion(left=0.2, top=0.8, right=0.5, bottom=0.3)


def test_desk_region_from_drag_normalizes_preview_coordinates():
    assert DeskRegion.from_drag((320, 120), (640, 480), 640, 480) == DeskRegion(
        left=0.5, top=0.25, right=1.0, bottom=1.0,
    )


def test_calibration_requires_an_available_unpaused_preview():
    runtime = ContextRuntime(device_id="local-laptop", desk_calibration_available=False)
    assert runtime.begin_desk_calibration() is False

    runtime = ContextRuntime(device_id="local-laptop", desk_calibration_available=True)
    assert runtime.begin_desk_calibration() is True
    runtime.set_paused(True)
    assert runtime.begin_desk_calibration() is False
    assert runtime.desk_region_status()["calibrating"] is False


def test_low_quality_frames_are_not_camera_absence():
    dark = np.zeros((120, 160, 3), dtype=np.uint8)
    covered_camera = np.full((120, 160, 3), 96, dtype=np.uint8)
    textured_scene = np.tile(np.linspace(35, 215, 160, dtype=np.uint8), (120, 1))
    textured_scene = np.repeat(textured_scene[:, :, None], 3, axis=2)

    assert not is_camera_frame_usable(dark)
    assert not is_camera_frame_usable(covered_camera)
    assert is_camera_frame_usable(textured_scene)


def test_desk_region_store_round_trips_only_calibration(tmp_path):
    store = DeskRegionStore(tmp_path / "desk_region.json")
    region = DeskRegion(left=0.1, top=0.2, right=0.9, bottom=0.8)

    assert store.load() is None
    store.save(region)

    assert store.load() == region
    assert set(json.loads((tmp_path / "desk_region.json").read_text())) == {
        "left", "top", "right", "bottom"
    }


def test_desk_region_store_fails_closed_on_malformed_data(tmp_path):
    path = tmp_path / "desk_region.json"
    path.write_text('{"left":0,"top":0,"right":2,"bottom":1}', encoding="utf-8")

    assert DeskRegionStore(path).load() is None


def test_unconfigured_desk_region_stays_unknown_until_setup():
    runtime = ContextRuntime(device_id="local-laptop", boot_id="boot-a")
    collector = CompanionContextCollector(runtime, desktop_adapter=type("Desktop", (), {"sample": lambda _: None})())

    for tick in range(241):
        collector.observe_face(False, tick * 0.05)

    field = runtime.read_snapshot().fields["deskPresence"]
    assert field.value == "unknown"
    assert field.freshness == "unavailable"


def test_dashboard_calibration_api_reports_preview_availability(tmp_path):
    from config import Config, StorageConfig
    from dashboard import create_app

    class ContextClient:
        def begin_desk_calibration(self):
            return False

    client = create_app(
        Config(storage=StorageConfig(database=tmp_path / "phone_watch.db")),
        context_client=ContextClient(),
    ).test_client()

    response = client.post("/api/context/desk-region/calibration", json={"enabled": True})

    assert response.status_code == 409
    assert response.json["error"] == "Calibration cannot start. Resume context and make sure the live preview is available."


def test_dashboard_explains_camera_region_calibration_and_unknown_states(tmp_path):
    from config import Config, StorageConfig
    from dashboard import create_app

    page = create_app(
        Config(storage=StorageConfig(database=tmp_path / "phone_watch.db")),
        context_client=type("ContextClient", (), {})(),
    ).test_client().get("/").get_data(as_text=True)

    assert 'id="desk-calibration-start"' in page
    assert "drag a rectangle around your desk" in page
    assert "it does not identify who is visible" in page
    assert "obstructed view stays unknown" in page


def test_dashboard_can_start_or_cancel_preview_calibration(tmp_path):
    from config import Config, StorageConfig
    from dashboard import create_app

    class ContextClient:
        def __init__(self):
            self.started = False
            self.cancelled = False

        def begin_desk_calibration(self):
            self.started = True
            return True

        def cancel_desk_calibration(self):
            self.cancelled = True

        def desk_region_status(self):
            return {
                "available": True,
                "calibrated": False,
                "calibrating": self.started and not self.cancelled,
                "region": None,
            }

    context = ContextClient()
    app = create_app(Config(storage=StorageConfig(database=tmp_path / "phone_watch.db")),
                     context_client=context)
    client = app.test_client()

    start = client.post("/api/context/desk-region/calibration", json={"enabled": True})
    cancel = client.post("/api/context/desk-region/calibration", json={"enabled": False})

    assert start.status_code == 200
    assert start.json["calibrating"] is True
    assert context.started
    assert cancel.status_code == 200
    assert context.cancelled
    assert client.get("/api/context/desk-region").json["desk_region"]["available"] is True
