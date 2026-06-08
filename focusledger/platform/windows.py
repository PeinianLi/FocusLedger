from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path

from focusledger.models import ActiveWindowSnapshot

from .base import PlatformAdapter


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("dwTime", wintypes.DWORD),
    ]


class WindowsPlatformAdapter(PlatformAdapter):
    def __init__(self) -> None:
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def get_active_window(self) -> ActiveWindowSnapshot:
        captured_at = self.get_now()
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return ActiveWindowSnapshot(
                captured_at=captured_at,
                process_id=None,
                process_name=None,
                executable_path=None,
                window_title=None,
                window_handle=None,
            )

        title = self._get_window_title(hwnd)
        process_id = self._get_process_id(hwnd)
        executable_path = self._get_executable_path(process_id) if process_id else None
        process_name = Path(executable_path).name if executable_path else None
        return ActiveWindowSnapshot(
            captured_at=captured_at,
            process_id=process_id,
            process_name=process_name,
            executable_path=executable_path,
            window_title=title,
            window_handle=str(hwnd),
        )

    def get_idle_seconds(self) -> float:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not self.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        tick_count = self.kernel32.GetTickCount()
        return max(0.0, float(tick_count - info.dwTime) / 1000.0)

    def _get_window_title(self, hwnd: int) -> str:
        length = self.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self.user32.GetWindowTextW(hwnd, buffer, length + 1)
        return buffer.value

    def _get_process_id(self, hwnd: int) -> int | None:
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) if pid.value else None

    def _get_executable_path(self, process_id: int) -> str | None:
        handle = self.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = self.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            if not ok:
                return None
            return buffer.value
        finally:
            self.kernel32.CloseHandle(handle)
