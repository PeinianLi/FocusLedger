from __future__ import annotations

import logging
import subprocess

from focusledger.models import ActiveWindowSnapshot

from .base import PlatformAdapter


LOGGER = logging.getLogger(__name__)

SOURCE = "macos_active_window"


class MacOSPlatformAdapter(PlatformAdapter):
    """macOS active-window / idle collector.

    Two backends are supported:

    * ``pyobjc``     - uses AppKit + Quartz when PyObjC is installed. Cleanest
                       and gets the front app without any special permission.
                       Reports ``kCGWindowNumber`` as the window handle, which
                       is what the privacy layer needs to keep treating a
                       browser window as private after its title stops carrying
                       a private marker.
    * ``subprocess`` - pure stdlib fallback using the built-in ``lsappinfo``,
                       ``osascript`` and ``ioreg`` commands. Needs no third-party
                       packages, so it works even when PyPI is unreachable. No
                       macOS command-line tool exposes a stable window id, so
                       ``window_handle`` is ``None`` here and private-window
                       tracking degrades to matching the current title only.

    PyObjC is a declared dependency on darwin, so a normal install lands on the
    pyobjc backend; the subprocess one is for running from source without it.

    In both backends the window title requires a permission grant
    (Screen Recording for pyobjc, Accessibility for the subprocess backend).
    Without it the app-level data is still collected and the title is ``None``.
    """

    def __init__(self) -> None:
        self._backend = self._select_backend()
        LOGGER.info("macOS collector backend: %s", self._backend)
        if self._backend == "subprocess":
            LOGGER.warning(
                "PyObjC not found, using the subprocess collector. Window handles are "
                "unavailable in this mode, so a private/incognito browser window is only "
                "redacted while its own title still carries a private marker: once it "
                "navigates to a page, the page title is stored. Install "
                "pyobjc-framework-Cocoa and pyobjc-framework-Quartz for full privacy tracking."
            )

    @staticmethod
    def _select_backend() -> str:
        try:
            import AppKit  # noqa: F401
            import Quartz  # noqa: F401

            return "pyobjc"
        except ImportError:
            return "subprocess"

    def get_active_window(self) -> ActiveWindowSnapshot:
        if self._backend == "pyobjc":
            return self._active_window_pyobjc()
        return self._active_window_subprocess()

    def get_idle_seconds(self) -> float:
        if self._backend == "pyobjc":
            return self._idle_pyobjc()
        return self._idle_subprocess()

    # ------------------------------------------------------------------
    # PyObjC backend
    # ------------------------------------------------------------------
    def _active_window_pyobjc(self) -> ActiveWindowSnapshot:
        import AppKit

        captured_at = self.get_now()
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return self._empty_snapshot(captured_at)

        process_id = int(app.processIdentifier())
        process_name = str(app.localizedName()) if app.localizedName() else None
        bundle_url = app.bundleURL()
        executable_path = str(bundle_url.path()) if bundle_url is not None else None

        window_title, window_handle = self._front_window_pyobjc(process_id)

        return ActiveWindowSnapshot(
            captured_at=captured_at,
            process_id=process_id,
            process_name=process_name,
            executable_path=executable_path,
            window_title=window_title,
            window_handle=window_handle,
            source=SOURCE,
        )

    def _front_window_pyobjc(self, process_id: int) -> tuple[str | None, str | None]:
        import Quartz

        options = (
            Quartz.kCGWindowListOptionOnScreenOnly
            | Quartz.kCGWindowListExcludeDesktopElements
        )
        window_list = Quartz.CGWindowListCopyWindowInfo(options, Quartz.kCGNullWindowID)
        if not window_list:
            return None, None

        for window in window_list:
            if window.get("kCGWindowOwnerPID") != process_id:
                continue
            if window.get("kCGWindowLayer", 0) != 0:
                continue
            name = window.get("kCGWindowName")
            number = window.get("kCGWindowNumber")
            return (str(name) if name else None), (str(number) if number is not None else None)

        return None, None

    def _idle_pyobjc(self) -> float:
        import Quartz

        return float(
            Quartz.CGEventSourceSecondsSinceLastEventType(
                Quartz.kCGEventSourceStateHIDSystemState,
                Quartz.kCGAnyInputEventType,
            )
        )

    # ------------------------------------------------------------------
    # Subprocess backend (no third-party packages required)
    # ------------------------------------------------------------------
    def _active_window_subprocess(self) -> ActiveWindowSnapshot:
        captured_at = self.get_now()
        asn = self._run(["lsappinfo", "front"]).strip()
        if not asn:
            return self._empty_snapshot(captured_at)

        process_name = self._lsappinfo_value(asn, "name")
        executable_path = self._lsappinfo_value(asn, "bundlepath")
        pid_raw = self._lsappinfo_value(asn, "pid")
        try:
            process_id = int(pid_raw) if pid_raw else None
        except ValueError:
            process_id = None

        title_pid, window_title = self._front_window_subprocess()
        # lsappinfo and osascript are separate processes running milliseconds
        # apart. If the front app changed in between, the title belongs to a
        # different process; drop it rather than file it under the wrong app.
        if (
            window_title
            and process_id is not None
            and title_pid is not None
            and title_pid != process_id
        ):
            LOGGER.debug(
                "front app changed while sampling (pid %s -> %s); dropping window title",
                process_id,
                title_pid,
            )
            window_title = None

        return ActiveWindowSnapshot(
            captured_at=captured_at,
            process_id=process_id,
            process_name=process_name,
            executable_path=executable_path,
            window_title=window_title,
            # No macOS command-line tool exposes a stable window id. See the
            # class docstring for what this costs the privacy layer.
            window_handle=None,
            source=SOURCE,
        )

    def _lsappinfo_value(self, asn: str, key: str) -> str | None:
        raw = self._run(["lsappinfo", "info", "-only", key, asn]).strip()
        # Output looks like: "name"="Google Chrome"  or  "pid"=1234
        if "=" not in raw:
            return None
        value = raw.split("=", 1)[1].strip().strip('"')
        return value or None

    def _front_window_subprocess(self) -> tuple[int | None, str | None]:
        """Return ``(owner_pid, window_title)`` for the frontmost window.

        The pid travels with the title so the caller can check the title still
        belongs to the app ``lsappinfo`` reported a moment earlier.
        """
        # Electron/Chromium-based apps (VS Code, Slack, Chrome, ...) return an
        # empty string for "name of front window", but expose the title via the
        # accessibility AXTitle attribute. Try AXTitle first, then fall back.
        script = (
            'tell application "System Events"\n'
            "    set frontApp to first application process whose frontmost is true\n"
            "    set frontPid to unix id of frontApp\n"
            "    try\n"
            '        set winTitle to value of attribute "AXTitle" of window 1 of frontApp\n'
            "    on error\n"
            "        try\n"
            "            set winTitle to name of front window of frontApp\n"
            "        on error\n"
            '            set winTitle to ""\n'
            "        end try\n"
            "    end try\n"
            '    if winTitle is missing value then set winTitle to ""\n'
            "    return (frontPid as text) & linefeed & winTitle\n"
            "end tell"
        )
        return self._parse_front_window(self._run(["osascript", "-e", script]))

    @staticmethod
    def _parse_front_window(output: str) -> tuple[int | None, str | None]:
        text = output.rstrip("\n")
        if not text:
            return None, None
        head, _, title = text.partition("\n")
        try:
            owner_pid = int(head.strip())
        except ValueError:
            # Not the shape the script emits, so nothing here can be trusted.
            return None, None
        title = title.strip()
        return owner_pid, (title or None)

    def _idle_subprocess(self) -> float:
        out = self._run(["ioreg", "-c", "IOHIDSystem"])
        for line in out.splitlines():
            if "HIDIdleTime" in line and "=" in line:
                try:
                    nanoseconds = int(line.split("=")[-1].strip())
                except ValueError:
                    continue
                return nanoseconds / 1_000_000_000
        # Must not return 0.0 here: that reads as "input just happened" and the
        # sample would score as fully attentive. Raising lets the agent record
        # the idle signal as unknown, which is what a failed read actually means.
        raise RuntimeError("ioreg did not report HIDIdleTime")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _run(args: list[str]) -> str:
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            LOGGER.warning("command failed: %s (%s)", " ".join(args), exc)
            return ""
        if result.returncode != 0:
            LOGGER.debug(
                "command exited %s: %s | %s",
                result.returncode,
                " ".join(args),
                result.stderr.strip(),
            )
        return result.stdout

    @staticmethod
    def _empty_snapshot(captured_at) -> ActiveWindowSnapshot:
        return ActiveWindowSnapshot(
            captured_at=captured_at,
            process_id=None,
            process_name=None,
            executable_path=None,
            window_title=None,
            window_handle=None,
            source=SOURCE,
        )
