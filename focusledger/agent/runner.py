from __future__ import annotations

import logging
import time
from datetime import datetime
from dataclasses import dataclass

from focusledger.agent.heartbeat import write_heartbeat
from focusledger.config import AppConfig
from focusledger.db.connection import connect
from focusledger.platform import get_platform_adapter
from focusledger.services.activity import record_activity_snapshot
from focusledger.services.alerts import DistractionTracker, notify, record_distraction
from focusledger.services.attention import AttentionPolicy
from focusledger.services.classify import classify, load_rule_context
from focusledger.services.workday import ACTIVE as WORKDAY_ACTIVE
from focusledger.services.workday import OVERTIME as WORKDAY_OVERTIME
from focusledger.services.workday import current_session
from focusledger.services.settings import get_setting, set_setting


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class AgentRunOptions:
    once: bool = False
    max_samples: int | None = None


def run_agent(config: AppConfig, options: AgentRunOptions) -> None:
    adapter = get_platform_adapter()
    attention_policy = AttentionPolicy.from_config(config)
    attention_policy.validate()
    sample_count = 0
    segment_gap_seconds = max(config.poll_interval_seconds * 3, 15)
    tracker = DistractionTracker(
        gaming_alert_seconds=config.gaming_alert_minutes * 60,
        entertainment_alert_seconds=config.entertainment_alert_minutes * 60,
        cooldown_seconds=config.notification_cooldown_seconds,
    )

    with connect(config.database_path) as conn:
        set_setting(conn, "agent_stop_requested", "false")
        write_heartbeat(conn, device_id=config.device_id, message="agent started")

        while True:
            if get_setting(conn, "agent_stop_requested", "false") == "true":
                write_heartbeat(conn, device_id=config.device_id, status="stopped", message="stop requested")
                break

            try:
                snapshot = adapter.get_active_window()
                idle_seconds = _read_idle_seconds(adapter)
                record_activity_snapshot(
                    conn,
                    device_id=config.device_id,
                    snapshot=snapshot,
                    segment_gap_seconds=segment_gap_seconds,
                    idle_seconds=idle_seconds,
                    attention_policy=attention_policy,
                )
                write_heartbeat(conn, device_id=config.device_id)
                _check_distraction(conn, config, tracker, snapshot)
            except Exception as exc:
                LOGGER.exception("agent sample failed")
                write_heartbeat(conn, device_id=config.device_id, status="error", message=str(exc))

            sample_count += 1
            if options.once or (options.max_samples is not None and sample_count >= options.max_samples):
                write_heartbeat(conn, device_id=config.device_id, status="stopped", message="agent completed")
                break

            time.sleep(config.poll_interval_seconds)


def _check_distraction(conn, config: AppConfig, tracker: DistractionTracker, snapshot) -> None:
    """Watch for a sustained distracting streak during an active workday.

    Runs inside the sampling loop, so it must stay cheap and must never raise:
    classification is a pure function over the snapshot, and any failure here is
    logged and dropped rather than costing us the sample.
    """
    try:
        session = current_session(conn)
        if not session or session["status"] not in (WORKDAY_ACTIVE, WORKDAY_OVERTIME):
            tracker.reset()
            return

        result = classify(snapshot.process_name, snapshot.window_title, load_rule_context(conn))
        now = datetime.now().astimezone()
        fired = tracker.observe(result.category, config.poll_interval_seconds, now)
        if not fired:
            return

        kind, streak_seconds = fired
        record_distraction(
            conn,
            device_id=config.device_id,
            workday_key=str(session["workday_key"]),
            kind=kind,
            category=result.category,
            process_name=snapshot.process_name,
            duration_seconds=streak_seconds,
            detected_at=now,
        )
        minutes = int(streak_seconds // 60)
        notify(
            "FocusLedger",
            f"{minutes} min of {result.category.replace('_', ' ')} during your workday.",
            sound=config.audio_enabled,
        )
    except Exception:
        LOGGER.debug("distraction check failed", exc_info=True)


def _read_idle_seconds(adapter) -> float | None:
    """Read seconds since last keyboard/mouse input. Returns None if the platform
    does not support idle detection, so a missing idle signal never drops the
    activity sample itself."""
    try:
        return float(adapter.get_idle_seconds())
    except Exception:
        LOGGER.debug("idle detection unavailable", exc_info=True)
        return None


def request_stop(config: AppConfig) -> None:
    with connect(config.database_path) as conn:
        set_setting(conn, "agent_stop_requested", "true")


def request_start(config: AppConfig) -> None:
    with connect(config.database_path) as conn:
        set_setting(conn, "agent_stop_requested", "false")


def mark_agent_stopped(config: AppConfig, message: str) -> None:
    with connect(config.database_path) as conn:
        write_heartbeat(
            conn,
            device_id=config.device_id,
            status="stopped",
            message=message,
        )
