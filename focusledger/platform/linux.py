from __future__ import annotations

from focusledger.models import ActiveWindowSnapshot

from .base import PlatformAdapter


class LinuxPlatformAdapter(PlatformAdapter):
    def get_active_window(self) -> ActiveWindowSnapshot:
        raise RuntimeError("Linux collection is reserved for a later phase.")

    def get_idle_seconds(self) -> float:
        raise RuntimeError("Linux idle detection is reserved for a later phase.")
