from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone, tzinfo
from dataclasses import dataclass

from focusledger.agent.heartbeat import HeartbeatThrottle, write_heartbeat
from focusledger.config import AppConfig
from focusledger.db.connection import connect
from focusledger.platform import get_platform_adapter
from focusledger.services.activity import record_activity_snapshot
from focusledger.services.alerts import DistractionTracker, notify, record_distraction
from focusledger.services.attention import AttentionPolicy
from focusledger.services.classify import classify, load_rule_context
from focusledger.services.retention import apply_retention
from focusledger.services.workday import ACTIVE as WORKDAY_ACTIVE
from focusledger.services.workday import OVERTIME as WORKDAY_OVERTIME
from focusledger.services.workday import current_session
from focusledger.services.settings import get_setting, set_setting


LOGGER = logging.getLogger(__name__)
RETENTION_RETRY_SECONDS = 300


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

    heartbeats = HeartbeatThrottle()

    with connect(config.database_path) as conn:
        set_setting(conn, "agent_stop_requested", "false")
        heartbeats.beat(conn, device_id=config.device_id, message="agent started")
        last_retention_date: date | None = None
        next_retention_attempt = 0.0

        while True:
            if get_setting(conn, "agent_stop_requested", "false") == "true":
                write_heartbeat(conn, device_id=config.device_id, status="stopped", message="stop requested")
                break

            current_local_date = _system_local_date()
            monotonic_now = time.monotonic()
            if (
                current_local_date != last_retention_date
                and monotonic_now >= next_retention_attempt
            ):
                try:
                    result = apply_retention(
                        conn,
                        retention_days=config.raw_retention_days,
                        poll_interval_seconds=config.poll_interval_seconds,
                        attention_policy=attention_policy,
                    )
                    LOGGER.info(
                        "retention complete archived_days=%s raw_deleted=%s "
                        "heartbeats_deleted=%s activity_segments=%s "
                        "attention_segments=%s",
                        result.archived_days,
                        result.raw_events_deleted,
                        result.heartbeats_deleted,
                        result.activity_segments_written,
                        result.attention_segments_written,
                    )
                    last_retention_date = current_local_date
                    next_retention_attempt = 0.0
                except Exception:
                    LOGGER.exception("retention failed; retrying in five minutes")
                    next_retention_attempt = (
                        monotonic_now + RETENTION_RETRY_SECONDS
                    )

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
                heartbeats.beat(conn, device_id=config.device_id)
                _check_distraction(conn, config, tracker, snapshot)
            except Exception as exc:
                LOGGER.exception("agent sample failed")
                heartbeats.beat(conn, device_id=config.device_id, status="error", message=str(exc))

            sample_count += 1
            if options.once or (options.max_samples is not None and sample_count >= options.max_samples):
                write_heartbeat(conn, device_id=config.device_id, status="stopped", message="agent completed")
                break

            time.sleep(config.poll_interval_seconds)


def _system_local_date(
    now: datetime | None = None,
    local_timezone: tzinfo | None = None,
) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if local_timezone is None:
        return current.astimezone().date()
    return current.astimezone(local_timezone).date()


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

        result = classify(
            snapshot.process_name,
            snapshot.window_title,
            load_rule_context(conn),
            executable_path=snapshot.executable_path,
        )
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
