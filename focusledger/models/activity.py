from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class ActiveWindowSnapshot:
    captured_at: datetime
    process_id: int | None
    process_name: str | None
    executable_path: str | None
    window_title: str | None
    window_handle: str | None
    source: str = "windows_active_window"
