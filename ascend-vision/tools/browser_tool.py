"""Google Chrome and web navigation tool for AI Diva."""
import logging
import os
from pathlib import Path
import subprocess
import urllib.parse
import webbrowser

LOG = logging.getLogger(__name__)

CHROME_PATHS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    Path(os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")),
]


def get_chrome_executable() -> Path | None:
    """Returns the Path to chrome.exe if present on the system, else None."""
    for p in CHROME_PATHS:
        if p.exists() and p.is_file():
            return p
    return None


def open_url(url: str) -> str:
    """Navigates directly to the specified URL in Google Chrome.

    Returns:
        Spoken confirmation string for AI Diva persona.
    """
    clean_url = url.strip()
    if not clean_url:
        return "No URL provided, CB."
    if not clean_url.startswith(("http://", "https://")):
        clean_url = f"https://{clean_url}"

    chrome = get_chrome_executable()
    LOG.info("Opening URL '%s' (chrome=%s)", clean_url, bool(chrome))

    if chrome:
        try:
            subprocess.Popen([str(chrome), clean_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opening that page for you now, CB: {clean_url}"
        except Exception as exc:
            LOG.warning("Failed to launch Chrome executable (%s), falling back to default browser", exc)

    webbrowser.open(clean_url)
    return f"Opened the URL for you, CB: {clean_url}"


def search_web(query: str) -> str:
    """Opens Google Chrome to search for the given query.

    Returns:
        Spoken confirmation string for AI Diva persona.
    """
    clean_query = query.strip()
    if not clean_query:
        return "You didn't give me anything to search for, CB."

    encoded = urllib.parse.quote_plus(clean_query)
    search_url = f"https://www.google.com/search?q={encoded}"
    open_url(search_url)
    return f"Searching Google for '{clean_query}', CB. Prepare to be enlightened."
