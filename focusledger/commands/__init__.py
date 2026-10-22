"""Per-domain CLI command groups, registered by ``focusledger.cli``."""

from focusledger.commands import (
    analyst_cmd,
    classify_cmd,
    deliverable_cmd,
    review_cmd,
    system_cmd,
    workday_cmd,
)

REGISTRARS = (
    workday_cmd.register,
    classify_cmd.register,
    deliverable_cmd.register,
    review_cmd.register,
    system_cmd.register,
    analyst_cmd.register,
)

__all__ = ["REGISTRARS"]
