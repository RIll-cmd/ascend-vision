"""Spotify and media playback controller for AI Diva."""
import ctypes
import logging
from typing import Optional

LOG = logging.getLogger(__name__)

VK_MEDIA_PLAY_PAUSE = 0xB3
KEYEVENTF_KEYUP = 0x0002

_last_state: bool = False  # Track toggle state (True = playing, False = paused)


def get_spotify_status() -> dict:
    """Inspects open windows to detect Spotify running state and current track title."""
    status = {"running": False, "is_playing": False, "title": "", "track": ""}
    try:
        import win32gui

        def enum_handler(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                # Check for Spotify window titles
                if title and ("spotify" in title.lower() or " - " in title):
                    class_name = win32gui.GetClassName(hwnd)
                    if "Chrome_WidgetWin" in class_name or "Spotify" in title:
                        status["running"] = True
                        status["title"] = title
                        if " - " in title and not title.lower().startswith("spotify"):
                            status["is_playing"] = True
                            status["track"] = title

        win32gui.EnumWindows(enum_handler, None)
    except Exception as exc:
        LOG.debug("Window enumeration for Spotify status check failed: %s", exc)

    return status


def spotify_play_pause(action: Optional[str] = None) -> tuple[bool, str]:
    """Toggles or sets Spotify play/pause state via Windows media keys.

    Args:
        action: Optional explicit action ('play', 'pause', or None for toggle).

    Returns:
        tuple[bool, str]: (success, diva_confirmation_message)
    """
    global _last_state
    status = get_spotify_status()

    # Determine whether we are pausing or resuming
    if action == "pause":
        is_pausing = True
    elif action == "play":
        is_pausing = False
    elif status["running"] and status["track"]:
        # Currently playing a track, so toggle will pause
        is_pausing = True
    else:
        # Toggle based on alternating state
        _last_state = not _last_state
        is_pausing = not _last_state

    # Trigger OS VK_MEDIA_PLAY_PAUSE key event
    try:
        import win32api
        win32api.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
        win32api.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)
    except Exception as win_err:
        LOG.debug("win32api keybd_event failed (%s), falling back to ctypes", win_err)
        try:
            user32 = ctypes.windll.user32
            user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
            user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)
        except Exception as c_err:
            LOG.error("Failed to send media play/pause key: %s", c_err)
            return False, "Failed to send media key command, CB."

    if is_pausing:
        msg = "Pausing your music, CB. Enjoy the blessing of my voice."
    else:
        msg = "Resuming the beats. You're welcome."

    LOG.info("Spotify media key triggered (is_pausing=%s): %s", is_pausing, msg)
    return True, msg
