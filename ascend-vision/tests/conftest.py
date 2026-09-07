"""Main-loop regression tests use real temporary SQLite, without desktop hooks."""
import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(request, tmp_path, monkeypatch):
    if request.node.module.__name__ not in ('test_main', 'test_preview'):
        return
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    monkeypatch.chdir(tmp_path)
    class TestControls:
        def __init__(self, *args):
            pass
        def start(self):
            pass
        def refresh(self):
            pass
        def close(self):
            pass
    monkeypatch.setattr('main.DesktopControls', TestControls)
