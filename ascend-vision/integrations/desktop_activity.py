"""Coarse foreground category, input-idle, and lock state for Windows."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import PureWindowsPath
import ctypes
import sys
from ctypes import wintypes


@dataclass(frozen=True)
class DesktopActivitySample:
    desktop_activity: str
    foreground_category: str
    available: bool


class _WindowsProbe:
    """Small Win32 boundary; never returns a title or full executable path."""

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    DESKTOP_READOBJECTS = 0x0001
    DESKTOP_SWITCHDESKTOP = 0x0100
    UOI_NAME = 2

    def __init__(self):
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32.GetTickCount.restype = wintypes.DWORD
        self._user32.GetLastInputInfo.argtypes = [ctypes.c_void_p]
        self._user32.GetLastInputInfo.restype = wintypes.BOOL
        self._user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._user32.OpenInputDesktop.restype = wintypes.HANDLE
        self._user32.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetUserObjectInformationW.restype = wintypes.BOOL
        self._user32.CloseDesktop.argtypes = [wintypes.HANDLE]
        self._user32.CloseDesktop.restype = wintypes.BOOL

    def foreground_executable(self) -> str | None:
        hwnd = self._user32.GetForegroundWindow()
        if not hwnd:
            return None
        process_id = wintypes.DWORD()
        if not self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id)) or not process_id.value:
            return None
        handle = self._kernel32.OpenProcess(self.PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value)
        if not handle:
            return None
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if not self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return None
            return PureWindowsPath(buffer.value[:length.value]).name
        finally:
            self._kernel32.CloseHandle(handle)

    def idle_milliseconds(self) -> int | None:
        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not self._user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        current = self._kernel32.GetTickCount()
        return (int(current) - int(info.dwTime)) & 0xFFFFFFFF

    def is_locked(self) -> bool | None:
        handle = self._user32.OpenInputDesktop(
            0, False, self.DESKTOP_READOBJECTS | self.DESKTOP_SWITCHDESKTOP,
        )
        if not handle:
            return None
        try:
            name = ctypes.create_unicode_buffer(256)
            needed = wintypes.DWORD()
            if not self._user32.GetUserObjectInformationW(
                handle, self.UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed),
            ):
                return None
            return name.value.casefold() != "default"
        finally:
            self._user32.CloseDesktop(handle)


class WindowsActivityAdapter:
    CATEGORY_BY_PROCESS = {
        "code.exe": "development",
        "pycharm64.exe": "development",
        "idea64.exe": "development",
        "devenv.exe": "development",
        "WindowsTerminal.exe".casefold(): "development",
        "powershell.exe": "development",
        "pwsh.exe": "development",
        "slack.exe": "communication",
        "discord.exe": "communication",
        "teams.exe": "communication",
        "outlook.exe": "communication",
        "chrome.exe": "browser_unspecified",
        "msedge.exe": "browser_unspecified",
        "firefox.exe": "browser_unspecified",
        "brave.exe": "browser_unspecified",
    }

    def __init__(self, *, probe=None, platform_name: str | None = None,
                 idle_after_seconds: float = 60.0):
        if (isinstance(idle_after_seconds, bool) or not isinstance(idle_after_seconds, (int, float))
                or not math.isfinite(idle_after_seconds) or idle_after_seconds <= 0):
            raise ValueError("idle_after_seconds must be positive")
        self.platform_name = platform_name or sys.platform
        self.probe = probe
        if self.probe is None and self.platform_name == "win32":
            self.probe = _WindowsProbe()
        self.idle_after_ms = int(idle_after_seconds * 1000)

    def sample(self) -> DesktopActivitySample:
        if self.platform_name != "win32" or self.probe is None:
            return DesktopActivitySample("unavailable", "unknown", False)
        try:
            locked = self.probe.is_locked()
            if locked is True:
                return DesktopActivitySample("locked", "unknown", True)
            if locked is not False:
                return DesktopActivitySample("unavailable", "unknown", False)
            idle_ms = self.probe.idle_milliseconds()
            executable = self.probe.foreground_executable()
        except (OSError, AttributeError, ValueError):
            return DesktopActivitySample("unavailable", "unknown", False)
        if idle_ms is None or isinstance(idle_ms, bool) or not isinstance(idle_ms, int) or idle_ms < 0:
            activity = "unavailable"
        else:
            activity = "input_idle" if idle_ms >= self.idle_after_ms else "input_active"
        if executable is None:
            category = "unknown"
        else:
            category = self.CATEGORY_BY_PROCESS.get(
                PureWindowsPath(str(executable)).name.casefold(), "other",
            )
        return DesktopActivitySample(activity, category, activity != "unavailable")
