"""Phase 9: start the agent when the machine starts.

Uses each platform's own mechanism rather than a resident supervisor process:
launchd on macOS, Task Scheduler on Windows, systemd --user on Linux. No third
party dependency, and uninstalling leaves nothing behind.

Every function reports what it did instead of assuming success, because a
failure here is silent by nature — the user only finds out days later when the
day is empty.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

LABEL = "com.focusledger.agent"
TASK_NAME = "FocusLedgerAgent"
SERVICE_NAME = "focusledger-agent"


class AutostartError(RuntimeError):
    pass


@dataclass(frozen=True)
class AutostartStatus:
    supported: bool
    installed: bool
    platform_name: str
    location: str | None = None
    detail: str = ""


def _python_executable() -> str:
    return sys.executable or "python3"


def _agent_command(config) -> list[str]:
    # --background-child runs the loop in the foreground of the spawned process,
    # which is what a service manager wants; it supervises the process itself.
    return [_python_executable(), "-m", "focusledger", "start", "--background-child"]


def _environment(config) -> dict[str, str]:
    env = {"FOCUSLEDGER_DATA_DIR": str(config.data_dir)}
    path = os.environ.get("PATH")
    if path:
        env["PATH"] = path
    return env


# --------------------------------------------------------------------------
# macOS — launchd
# --------------------------------------------------------------------------
def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist_path() -> Path:
    return _launch_agents_dir() / f"{LABEL}.plist"


def _install_launchd(config) -> AutostartStatus:
    path = _plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = config.data_dir
    contents = {
        "Label": LABEL,
        "ProgramArguments": _agent_command(config),
        "EnvironmentVariables": _environment(config),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_dir / "agent.stdout.log"),
        "StandardErrorPath": str(log_dir / "agent.stderr.log"),
        "ProcessType": "Background",
    }
    path.write_bytes(plistlib.dumps(contents))

    # bootout first so a re-install picks up a changed command.
    subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
    result = subprocess.run(["launchctl", "load", str(path)], check=False, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise AutostartError(f"launchctl load failed: {detail or result.returncode}")
    return AutostartStatus(True, True, "macOS (launchd)", str(path), "loaded")


def _uninstall_launchd() -> AutostartStatus:
    path = _plist_path()
    if path.exists():
        subprocess.run(["launchctl", "unload", str(path)], check=False, capture_output=True)
        path.unlink()
        return AutostartStatus(True, False, "macOS (launchd)", str(path), "removed")
    return AutostartStatus(True, False, "macOS (launchd)", str(path), "was not installed")


def _status_launchd() -> AutostartStatus:
    path = _plist_path()
    if not path.exists():
        return AutostartStatus(True, False, "macOS (launchd)", str(path), "not installed")
    result = subprocess.run(["launchctl", "list"], check=False, capture_output=True)
    loaded = LABEL in result.stdout.decode("utf-8", "replace")
    return AutostartStatus(
        True, True, "macOS (launchd)", str(path),
        "loaded" if loaded else "installed but not loaded (log out and back in)",
    )


# --------------------------------------------------------------------------
# Windows — Task Scheduler
# --------------------------------------------------------------------------
def _install_schtasks(config) -> AutostartStatus:  # pragma: no cover - windows only
    command = " ".join(f'"{part}"' for part in _agent_command(config))
    result = subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "ONLOGON", "/TN", TASK_NAME, "/TR", command],
        check=False, capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise AutostartError(f"schtasks /Create failed: {detail or result.returncode}")
    return AutostartStatus(True, True, "Windows (Task Scheduler)", TASK_NAME, "registered")


def _uninstall_schtasks() -> AutostartStatus:  # pragma: no cover - windows only
    result = subprocess.run(
        ["schtasks", "/Delete", "/F", "/TN", TASK_NAME], check=False, capture_output=True
    )
    installed = result.returncode != 0
    return AutostartStatus(
        True, installed, "Windows (Task Scheduler)", TASK_NAME,
        "removed" if not installed else "was not installed",
    )


def _status_schtasks() -> AutostartStatus:  # pragma: no cover - windows only
    result = subprocess.run(
        ["schtasks", "/Query", "/TN", TASK_NAME], check=False, capture_output=True
    )
    return AutostartStatus(
        True, result.returncode == 0, "Windows (Task Scheduler)", TASK_NAME,
        "registered" if result.returncode == 0 else "not installed",
    )


# --------------------------------------------------------------------------
# Linux — systemd --user
# --------------------------------------------------------------------------
def _unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{SERVICE_NAME}.service"


def _install_systemd(config) -> AutostartStatus:
    if not shutil.which("systemctl"):
        raise AutostartError("systemctl not found; autostart is unsupported on this system")
    path = _unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    command = " ".join(_agent_command(config))
    environment = "\n".join(f'Environment="{k}={v}"' for k, v in _environment(config).items())
    path.write_text(
        "[Unit]\n"
        "Description=FocusLedger collection agent\n\n"
        "[Service]\n"
        f"ExecStart={command}\n"
        f"{environment}\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=default.target\n",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, capture_output=True)
    result = subprocess.run(
        ["systemctl", "--user", "enable", "--now", f"{SERVICE_NAME}.service"],
        check=False, capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise AutostartError(f"systemctl enable failed: {detail or result.returncode}")
    return AutostartStatus(True, True, "Linux (systemd --user)", str(path), "enabled")


def _uninstall_systemd() -> AutostartStatus:
    path = _unit_path()
    if shutil.which("systemctl"):
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"{SERVICE_NAME}.service"],
            check=False, capture_output=True,
        )
    if path.exists():
        path.unlink()
        return AutostartStatus(True, False, "Linux (systemd --user)", str(path), "removed")
    return AutostartStatus(True, False, "Linux (systemd --user)", str(path), "was not installed")


def _status_systemd() -> AutostartStatus:
    path = _unit_path()
    if not path.exists():
        return AutostartStatus(True, False, "Linux (systemd --user)", str(path), "not installed")
    result = subprocess.run(
        ["systemctl", "--user", "is-enabled", f"{SERVICE_NAME}.service"],
        check=False, capture_output=True,
    )
    state = result.stdout.decode("utf-8", "replace").strip() or "unknown"
    return AutostartStatus(True, True, "Linux (systemd --user)", str(path), state)


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------
def install(config) -> AutostartStatus:
    system = platform.system()
    if system == "Darwin":
        return _install_launchd(config)
    if system == "Windows":
        return _install_schtasks(config)
    if system == "Linux":
        return _install_systemd(config)
    raise AutostartError(f"autostart is not supported on {system}")


def uninstall() -> AutostartStatus:
    system = platform.system()
    if system == "Darwin":
        return _uninstall_launchd()
    if system == "Windows":
        return _uninstall_schtasks()
    if system == "Linux":
        return _uninstall_systemd()
    raise AutostartError(f"autostart is not supported on {system}")


def status() -> AutostartStatus:
    system = platform.system()
    if system == "Darwin":
        return _status_launchd()
    if system == "Windows":
        return _status_schtasks()
    if system == "Linux":
        return _status_systemd()
    return AutostartStatus(False, False, system, None, "unsupported platform")
