"""Unit tests for browser tool, spotify tool, and Real Ascend companion bridge."""
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from tools.browser_tool import open_url, search_web
from tools.spotify_tool import spotify_play_pause, get_spotify_status
from tools import __all__ as tool_exports


def test_browser_tool_url_formatting():
    """Verify URL prepending and search query encoding."""
    with patch("tools.browser_tool.subprocess.Popen") as mock_popen, \
         patch("tools.browser_tool.get_chrome_executable", return_value=Path("C:/Chrome/chrome.exe")):
        res1 = open_url("example.com")
        assert "example.com" in res1
        mock_popen.assert_called_once()
        args, _ = mock_popen.call_args
        assert args[0] == ["C:\\Chrome\\chrome.exe", "https://example.com"]

    with patch("tools.browser_tool.open_url") as mock_open:
        res2 = search_web("quantum mechanics tutorials")
        assert "quantum mechanics tutorials" in res2
        mock_open.assert_called_once_with("https://www.google.com/search?q=quantum+mechanics+tutorials")


def test_spotify_tool_media_keys():
    """Verify play/pause toggles and returns Diva lines."""
    with patch("win32api.keybd_event", create=True) as mock_keybd:
        success, msg1 = spotify_play_pause(action="pause")
        assert success is True
        assert "Pausing your music, CB" in msg1

        success, msg2 = spotify_play_pause(action="play")
        assert success is True
        assert "Resuming the beats" in msg2

    status = get_spotify_status()
    assert isinstance(status, dict)
    assert "running" in status
    assert "is_playing" in status


def test_tools_do_not_export_a_direct_ascend_data_bridge():
    assert not any('ascend' in name for name in tool_exports)
