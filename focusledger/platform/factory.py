from __future__ import annotations

import sys

from .base import PlatformAdapter


def get_platform_adapter() -> PlatformAdapter:
    if sys.platform.startswith("win"):
        from .windows import WindowsPlatformAdapter

        return WindowsPlatformAdapter()
    if sys.platform == "darwin":
        from .macos import MacOSPlatformAdapter

        return MacOSPlatformAdapter()
    from .linux import LinuxPlatformAdapter

    return LinuxPlatformAdapter()
