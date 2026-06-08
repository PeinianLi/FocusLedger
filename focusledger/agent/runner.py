from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from focusledger.agent.heartbeat import write_heartbeat
from focusledger.config import AppConfig
from focusledger.db.connection import connect
from focusledger.platform import get_platform_adapter
from focusledger.services.activity import record_activity_snapshot
from focusledger.services.settings import get_setting, set_setting


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunOptions:
    once: bool = False
    max_samples: int | None = None


def run_agent(config: AppConfig, options: AgentRunOptions) -> None:
    adapter = get_platform_adapter()
    sample_count = 0
    segment_gap_seconds = max(config.poll_interval_seconds * 3, 15)

    with connect(config.database_path) as conn:
        set_setting(conn, "agent_stop_requested", "false")
        write_heartbeat(conn, device_id=config.device_id, message="agent started")

        while True:
            if get_setting(conn, "agent_stop_requested", "false") == "true":
                write_heartbeat(conn, device_id=config.device_id, status="stopped", message="stop requested")
                break

            try:
                snapshot = adapter.get_active_window()
                record_activity_snapshot(
                    conn,
                    device_id=config.device_id,
                    snapshot=snapshot,
                    segment_gap_seconds=segment_gap_seconds,
                )
                write_heartbeat(conn, device_id=config.device_id)
            except Exception as exc:
                LOGGER.exception("agent sample failed")
                write_heartbeat(conn, device_id=config.device_id, status="error", message=str(exc))

            sample_count += 1
            if options.once or (options.max_samples is not None and sample_count >= options.max_samples):
                write_heartbeat(conn, device_id=config.device_id, status="stopped", message="agent completed")
                break

            time.sleep(config.poll_interval_seconds)


def request_stop(config: AppConfig) -> None:
    with connect(config.database_path) as conn:
        set_setting(conn, "agent_stop_requested", "true")
