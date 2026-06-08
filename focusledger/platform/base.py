from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from focusledger.models import ActiveWindowSnapshot


class PlatformAdapter(ABC):
    @abstractmethod
    def get_active_window(self) -> ActiveWindowSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_idle_seconds(self) -> float:
        raise NotImplementedError

    def get_now(self) -> datetime:
        return datetime.now().astimezone()
