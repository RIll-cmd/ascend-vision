"""Multi-system integration tools for AI Diva in phone_watch."""
from .browser_tool import open_url, search_web
from .spotify_tool import spotify_play_pause, get_spotify_status

__all__ = [
    "open_url",
    "search_web",
    "spotify_play_pause",
    "get_spotify_status",
]
