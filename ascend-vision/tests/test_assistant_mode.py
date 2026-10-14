import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from assistant_mode import IdleCapture, NoCameraModel
from config import RuntimeConfig
from focus_ui import FocusUI

ROOT = Path(__file__).resolve().parents[1]


def test_the_idle_capture_ticks_without_a_camera():
    capture = IdleCapture(tick_seconds=0.05).start()
    first = capture.read(0, 1.0)
    began = time.monotonic()
    second = capture.read(first.sequence, 0.01)
    assert time.monotonic() - began < 0.5, 'a short timeout is respected'
    assert (first.sequence, second.sequence) == (1, 2)
    assert first.image.shape[:2] == (1, 1)
    capture.close()


def test_no_camera_models_see_nothing_and_turn_away_mode_off():
    model = NoCameraModel()
    assert model.detect(None) is None
    assert model.landmarks(None, 0.0) == []
    assert model.available is False
    model.warmup(640, 480)
    model.close()


def test_runtime_mode_is_watch_or_assistant():
    assert RuntimeConfig().mode == 'watch'
    assert RuntimeConfig(mode='assistant').mode == 'assistant'
    with pytest.raises(ValueError):
        RuntimeConfig(mode='sleep')


def test_the_fairy_window_shows_the_camera_as_off_in_assistant_mode():
    ui = FocusUI()
    ui.camera_enabled = False
    ui.publish(None)
    assert ui.health.snapshot()['capabilities']['camera'] == 'disabled'


def test_assistant_mode_runs_without_loading_any_camera_ai(tmp_path):
    script = textwrap.dedent(f'''
        import json, sys
        from pathlib import Path
        from config import Config, FeedbackConfig, RuntimeConfig, StorageConfig, VoiceCommandConfig, AgentRoomConfig
        from main import run
        config = Config(runtime=RuntimeConfig(mode='assistant'), feedback=FeedbackConfig(enabled=False),
                        voice_commands=VoiceCommandConfig(enabled=False), agent_room=AgentRoomConfig(enabled=False),
                        storage=StorageConfig(database=Path(r"{tmp_path}") / "s.db"))
        run(config, duration=1.0, assistant=True, voice_listener=False)
        print(json.dumps({{m: m in sys.modules for m in ("torch", "ultralytics", "mediapipe")}}))
    ''')
    done = subprocess.run([sys.executable, '-c', script], cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stderr[-2000:]
    assert json.loads(done.stdout.strip().splitlines()[-1]) == {'torch': False, 'ultralytics': False, 'mediapipe': False}
