"""Main-loop regression tests use real temporary SQLite, without desktop hooks."""
import ipaddress
import os
import socket

import pytest

# Tests must never spend the owner's AI quota or download models: no real .env,
# no provider keys, and no network connection beyond this computer.
PROVIDER_KEYS = ('GEMINI_API_KEY', 'GROQ_API_KEY', 'CEREBRAS_API_KEY')


def _is_local(address) -> bool:
    host = address[0] if isinstance(address, tuple) else address
    if not isinstance(host, str):
        return True  # Unix-style or already-resolved local endpoints.
    if host in ('localhost', '') or host.endswith('.localhost'):
        return True
    try:
        return ipaddress.ip_address(host.split('%', 1)[0]).is_loopback
    except ValueError:
        return False  # A public host name.


@pytest.fixture(autouse=True)
def no_real_keys_or_network(request, monkeypatch, tmp_path):
    saved = dict(os.environ)
    module = request.node.module.__name__
    if module != 'test_runtime_environment':  # that module tests .env loading with its own files
        monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    for name in PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
    real_connect = socket.socket.connect

    def local_only_connect(sock, address):
        if not _is_local(address):
            raise OSError(f'tests may not open network connections ({address[0] if isinstance(address, tuple) else address})')
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_only_connect)

    def no_download(*_args, **_kwargs):
        raise OSError('tests may not download model files')

    # The shared "key limited" record lives in a temporary folder during tests.
    monkeypatch.setattr('ai_key_status._STORE', None, raising=False)
    monkeypatch.setattr('ai_key_status.default_path', lambda: tmp_path / 'ai-key-status.sqlite3', raising=False)
    if module != 'test_tts_engine':  # that module fakes the HTTP response itself
        monkeypatch.setattr('tts_engine._download_file', no_download, raising=False)
    yield
    # load_dotenv() writes straight into os.environ; undo anything a test leaked.
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def isolated_runtime(request, tmp_path, monkeypatch):
    if request.node.module.__name__ not in ('test_main', 'test_preview'):
        return
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setenv('PYTHON_DOTENV_DISABLED', '1')
    monkeypatch.chdir(tmp_path)
    class TestControls:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            pass
        def refresh(self):
            pass
        def close(self):
            pass
    monkeypatch.setattr('main.DesktopControls', TestControls)
