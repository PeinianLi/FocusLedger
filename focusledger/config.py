from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from zoneinfo import ZoneInfo


APP_DIR_NAME = "FocusLedger"
CONFIG_FILE_NAME = "config.json"


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    database_path: Path
    log_path: Path
    log_level: str
    poll_interval_seconds: int
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
    timezone: str

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


def detect_timezone() -> str:
    return os.environ.get("TZ") or "America/New_York"


def build_default_config(data_dir: Path | None = None) -> AppConfig:
    root = data_dir or default_data_dir()
    return AppConfig(
        data_dir=root,
        database_path=root / "focusledger.sqlite3",
        log_path=root / "focusledger.log",
        log_level="INFO",
        poll_interval_seconds=5,
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
        timezone=detect_timezone(),
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
        timezone=str(merged["timezone"]),
    )


def save_config(config: AppConfig) -> None:
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config_path(config.data_dir).write_text(
        json.dumps(config.to_json_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_timezone(config: AppConfig) -> ZoneInfo:
    try:
        return ZoneInfo(config.timezone)
    except Exception:
        return ZoneInfo("UTC")
