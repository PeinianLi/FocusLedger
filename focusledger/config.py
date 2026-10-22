from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


APP_DIR_NAME = "FocusLedger"
CONFIG_FILE_NAME = "config.json"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    database_path: Path
    log_path: Path
    log_level: str
    poll_interval_seconds: int
    raw_retention_days: int
    idle_threshold_seconds: int
    low_activity_after_seconds: int
    attention_gap_after_seconds: int
    interrupted_after_seconds: int
    hard_idle_after_seconds: int
    attention_active_weight: float
    attention_low_activity_weight: float
    attention_gap_weight: float
    attention_interrupted_weight: float
    attention_hard_idle_weight: float
    platform: str
    device_id: str
    language: str
    # Phase 3: workday boundaries
    default_start_time: str
    default_offwork_time: str
    max_workday_hours: float
    # Phase 7: distraction alerts
    audio_enabled: bool
    notification_cooldown_seconds: int
    gaming_alert_minutes: float
    entertainment_alert_minutes: float
    # Phase 8: local LLM (Ollama-compatible HTTP endpoint)
    llm_enabled: bool
    llm_base_url: str
    llm_model: str
    llm_timeout_seconds: int
    # The analyst calls tools over several turns, which needs a larger model
    # than the one-shot review prose does.
    llm_analyst_model: str

    def to_json_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["data_dir"] = str(self.data_dir)
        data["database_path"] = str(self.database_path)
        data["log_path"] = str(self.log_path)
        return data


def default_data_dir() -> Path:
    override = os.environ.get("FOCUSLEDGER_DATA_DIR")
    if override:
        return Path(override).expanduser()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIR_NAME

    return Path.home() / f".{APP_DIR_NAME.lower()}"


def config_path(data_dir: Path | None = None) -> Path:
    return (data_dir or default_data_dir()) / CONFIG_FILE_NAME


def build_default_config(data_dir: Path | None = None) -> AppConfig:
    root = data_dir or default_data_dir()
    return AppConfig(
        data_dir=root,
        database_path=root / "focusledger.sqlite3",
        log_path=root / "focusledger.log",
        log_level="INFO",
        poll_interval_seconds=1,
        raw_retention_days=7,
        idle_threshold_seconds=300,
        low_activity_after_seconds=180,
        attention_gap_after_seconds=600,
        interrupted_after_seconds=1200,
        hard_idle_after_seconds=1800,
        attention_active_weight=1.0,
        attention_low_activity_weight=0.5,
        attention_gap_weight=0.0,
        attention_interrupted_weight=0.0,
        attention_hard_idle_weight=0.0,
        platform=os.name,
        device_id=f"device_{uuid.uuid4().hex[:12]}",
        language="zh",
        default_start_time="09:00",
        default_offwork_time="18:00",
        max_workday_hours=16.0,
        audio_enabled=True,
        notification_cooldown_seconds=900,
        gaming_alert_minutes=10.0,
        entertainment_alert_minutes=15.0,
        llm_enabled=True,
        llm_base_url="http://127.0.0.1:11434",
        llm_model="qwen2.5:1.5b",
        llm_timeout_seconds=120,
        llm_analyst_model="qwen3.5:9b",
    )


def ensure_config() -> AppConfig:
    cfg_path = config_path()
    if not cfg_path.exists():
        cfg = build_default_config(cfg_path.parent)
        save_config(cfg)
        return cfg
    return load_config()


def load_config() -> AppConfig:
    cfg_path = config_path()
    if not cfg_path.exists():
        return build_default_config(cfg_path.parent)

    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    data_dir = Path(raw["data_dir"])
    cfg = build_default_config(data_dir)
    merged = cfg.to_json_dict()
    merged.update(raw)
    return AppConfig(
        data_dir=Path(str(merged["data_dir"])),
        database_path=Path(str(merged["database_path"])),
        log_path=Path(str(merged["log_path"])),
        log_level=str(merged["log_level"]),
        poll_interval_seconds=int(merged["poll_interval_seconds"]),
        raw_retention_days=int(merged["raw_retention_days"]),
        idle_threshold_seconds=int(merged["idle_threshold_seconds"]),
        low_activity_after_seconds=int(merged["low_activity_after_seconds"]),
        attention_gap_after_seconds=int(merged["attention_gap_after_seconds"]),
        interrupted_after_seconds=int(merged["interrupted_after_seconds"]),
        hard_idle_after_seconds=int(merged["hard_idle_after_seconds"]),
        attention_active_weight=float(merged["attention_active_weight"]),
        attention_low_activity_weight=float(merged["attention_low_activity_weight"]),
        attention_gap_weight=float(merged["attention_gap_weight"]),
        attention_interrupted_weight=float(merged["attention_interrupted_weight"]),
        attention_hard_idle_weight=float(merged["attention_hard_idle_weight"]),
        platform=str(merged["platform"]),
        device_id=str(merged["device_id"]),
        language=str(merged["language"]),
        default_start_time=str(merged["default_start_time"]),
        default_offwork_time=str(merged["default_offwork_time"]),
        max_workday_hours=float(merged["max_workday_hours"]),
        audio_enabled=bool(merged["audio_enabled"]),
        notification_cooldown_seconds=int(merged["notification_cooldown_seconds"]),
        gaming_alert_minutes=float(merged["gaming_alert_minutes"]),
        entertainment_alert_minutes=float(merged["entertainment_alert_minutes"]),
        llm_enabled=bool(merged["llm_enabled"]),
        llm_base_url=str(merged["llm_base_url"]),
        llm_model=str(merged["llm_model"]),
        llm_timeout_seconds=int(merged["llm_timeout_seconds"]),
        llm_analyst_model=str(merged["llm_analyst_model"]),
    )


def save_config(config: AppConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config_path(config.data_dir).write_text(
        json.dumps(config.to_json_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
