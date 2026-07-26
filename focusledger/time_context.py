from __future__ import annotations

import os
import sys
import time as system_time
from dataclasses import dataclass
from datetime import datetime, time, timezone
from functools import lru_cache


@dataclass(frozen=True)
class CapturedTimeContext:
    captured_at: str
    captured_at_utc: str
    local_date: str
    timezone_id: str
    timezone_name: str
    utc_offset_seconds: int
    is_dst: bool
    local_day_start_utc: str


def capture_time_context(moment: datetime) -> CapturedTimeContext:
    if moment.tzinfo is None:
        local = moment.astimezone()
    else:
        local = moment

    offset = local.utcoffset()
    offset_seconds = int(offset.total_seconds()) if offset is not None else 0
    timezone_name = local.tzname() or "local"
    timestamp = local.timestamp()
    system_local = datetime.fromtimestamp(timestamp).astimezone()
    represents_system_zone = (
        system_local.utcoffset() == local.utcoffset()
        and system_local.tzname() == local.tzname()
    )

    if represents_system_zone:
        local_info = system_time.localtime(timestamp)
        is_dst = local_info.tm_isdst > 0
        timezone_id = _system_timezone_id(timezone_name, offset_seconds)
        day_start = datetime.combine(local.date(), time.min).astimezone(
            timezone.utc
        )
    else:
        daylight = local.dst()
        is_dst = bool(daylight and daylight.total_seconds())
        timezone_id = timezone_name
        day_start = datetime.combine(
            local.date(),
            time.min,
            tzinfo=local.tzinfo,
        ).astimezone(timezone.utc)

    return CapturedTimeContext(
        captured_at=local.isoformat(),
        captured_at_utc=local.astimezone(timezone.utc).isoformat(),
        local_date=local.date().isoformat(),
        timezone_id=timezone_id,
        timezone_name=timezone_name,
        utc_offset_seconds=offset_seconds,
        is_dst=is_dst,
        local_day_start_utc=day_start.isoformat(),
    )


@lru_cache(maxsize=16)
def _system_timezone_id(current_name: str, offset_seconds: int) -> str:
    if sys.platform == "win32":
        try:
            import winreg

            path = r"SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                value, _ = winreg.QueryValueEx(key, "TimeZoneKeyName")
            if value:
                return str(value).rstrip("\x00")
        except OSError:
            pass
    configured = os.environ.get("TZ")
    if configured:
        return configured
    return current_name or f"UTC{offset_seconds:+d}"
