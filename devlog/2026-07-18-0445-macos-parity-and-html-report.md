# Devlog: macOS Collection Parity and Interactive HTML Report

Updated: 2026-07-18 04:45  
Author: Lichen Zhu  
Baseline commit: `4c7f687 Reorganize docs and add CLI usage guide`

## Summary

This update has two work streams. The first closes real gaps between the
Windows and macOS collectors: three silent-bad-data bugs in the macOS
subprocess backend were found by executing the code paths (not just reading
them) and fixed. The second adds a new `report` CLI command that renders one
day of raw samples into a self-contained, interactive HTML dashboard — the
first visualization surface FocusLedger has beyond terminal text.

Windows code was intentionally untouched in both streams.

## Stream 1: macOS parity fixes

### How the bugs were found

Instead of trusting a code read, the macOS-relevant paths were exercised with
scripted probes: feeding macOS-shaped snapshots (`window_handle=None`, app-style
process names) through the privacy layer, replaying stored rows through the
redact command, and simulating `ioreg` failure in idle detection. All three
probes contradicted the documented behavior.

### Bug 1: private-browsing titles leaked (subprocess backend)

The privacy layer keeps treating a browser window as private after its title
stops carrying an Incognito marker — but that sticky rule keys on
`window_handle`, and the subprocess backend always stores `NULL` there. Result:
on macOS-without-PyObjC, an incognito window was only redacted while its title
still said "Incognito"; every page visited afterwards was stored in plain text.

No macOS command-line tool exposes a stable window id, so this cannot be fully
fixed in that backend. What changed:

- the backend now logs a clear startup warning describing exactly this
  degradation and pointing to PyObjC;
- the class docstring documents the difference between the two backends;
- README/CLI docs now state the limitation instead of implying parity.

The pyobjc backend (the default after `pip install`, since PyObjC is a declared
darwin dependency) reports `kCGWindowNumber` and has never had this problem.

### Bug 2: the redact command reported success while doing nothing

`privacy redact-private-browser-titles` filtered candidate rows with
`if row["window_handle"]`, so macOS subprocess data (all-NULL handles) produced
an empty handle set and an early return — while the CLI still printed
"Redacted…". Even rows whose titles carried an explicit Incognito marker were
skipped.

Fix: rows without a handle get their own rule — any browser row whose stored
title still carries a private marker is redacted directly. Rows with handles
keep the original sticky-window logic, so Windows behavior is unchanged. The
docs now state plainly that handle-less history from before the marker is
unrecoverable by this command (the data needed to associate windows was never
captured).

### Bug 3: failed idle reads scored as full attention

`_idle_subprocess` returned `0.0` when `ioreg` failed or `HIDIdleTime` was
missing. `0.0` means "input just happened", so a broken idle probe silently
counted as fully attentive time — the exact opposite of the proposal's
"conservative by default" rule, and a violation of the idle/unknown distinction.

Fix: the method now raises; the agent's existing `_read_idle_seconds` wrapper
catches it and records the sample with `idle_seconds=NULL`, which classifies as
`unknown` (weight 0). Windows keeps its own semantics untouched — its
`GetLastInputInfo` practically never fails, unlike subprocess calls.

### Also fixed: sampling race in the subprocess backend

`lsappinfo` (app identity) and `osascript` (window title) run milliseconds
apart; switching apps in between produced a snapshot with app A's name and app
B's title. The AppleScript now returns the owning pid together with the title,
and the collector drops the title when that pid does not match the sampled app
— a missing title beats a wrong one.

### Tests

`tests/test_macos_platform.py` (14 tests): osascript output parsing, idle
failure semantics (raise → agent records unknown), title-race dropping, and
handle-less redaction — including a regression test that handle data still uses
the sticky-window rule.

## Stream 2: `focusledger report` — interactive HTML dashboard

### Design constraints

- **Derived at view time.** `services/report.py` aggregates raw events through
  the same query-time derivation as `timeline`/`attention`; the HTML is a pure
  view over that JSON. Nothing derived is persisted.
- **Self-contained single file.** Inline CSS/JS/data, no network, no
  third-party libraries — consistent with local-first, and it opens from
  `file://`. (React was considered and rejected for this deliverable: a bundler
  and a runtime buy nothing for a generated single-file report. It becomes the
  right call if/when the Phase-9+ live dashboard happens.)
- **JSON injected into a `type="application/json"` script tag** with `<`
  escaped, so a hostile window title containing `</script>` cannot break out.

### The interaction model (trading-desk pattern)

Three time-aligned rows share one zoom/pan state: the **focus skyline** (bar
height = attention weight, color = state), a **focus-density row** (15-minute
buckets of effective-time ratio — the "volume" row), and **application
swimlanes** (one lane per top app). Below them, a **minimap** of the whole day
with a draggable/brushable viewport window.

- Scroll = cursor-anchored zoom (floor: 30 minutes), drag = pan, shift+drag =
  range-select zoom, double-click/Reset/Esc = animated reset, arrow keys pan,
  `+`/`-` zoom.
- Hover drives a crosshair across all three rows, a time bubble on the axis, a
  floating tooltip (state + weight + segment duration + max idle; app +
  segment minutes + window title), and a fixed readout bar.
- The State Breakdown and Applications panels re-aggregate over the visible
  range as you zoom; header shows the range.
- **Deep-work sessions** (≥25 min sustained weight ≥ 0.5, tolerating dips
  <5 min) are derived in the page and listed with duration, effective time and
  dominant app; clicking one animates the zoom to it. Sessions also appear as
  ticks under the minimap. Stat tiles gained "Deep work" and "App switches"
  (process-level transitions per hour — a fragmentation signal).
- Long zero-weight stretches (≥20 min) are annotated directly on the skyline
  ("· 45m hard idle ·"), so the day reads without hovering.

### A bug worth recording: `offsetX`

The first interactive iteration read cursor position from `ev.offsetX`, which
is relative to the event *target* — hovering over an SVG rect made every
coordinate wrong, killing the crosshair/readout entirely. Fixed by computing
positions as `clientX - getBoundingClientRect().left` and making all SVG marks
`pointer-events: none` so the stage owns every event. A static review script
now asserts, among other things, that `offsetX` never reappears in the
template.

### Static review harness

No browser or Node is available in the dev environment, so the template is
checked by a Python script on every change: forbidden APIs (`offsetX`), every
`getElementById`/selector id present in the markup, bracket balance in the
script block, `MIN_SPAN = 30` enforced in `clampView`, all interaction
functions defined exactly once and called, all three chart stages bound.
Runtime feel still requires opening the page — the script only rules out the
"dead page" class of failures.

### CLI

```bash
python -m focusledger report --today
python -m focusledger report --date 2026-07-16 --output ~/Desktop/focus.html
```

Default output is `<data_dir>/report-<date>.html`; the command prints a
`file://` link. The template ships as package data
(`focusledger/report/template.html`).

## Files touched

- `focusledger/platform/macos.py` — idle failure semantics, title-race pid
  check, degradation warning, docstring
- `focusledger/services/privacy.py` — handle-less redaction path
- `focusledger/services/report.py` — new: report data aggregation + rendering
- `focusledger/report/template.html` — new: the dashboard
- `focusledger/cli.py` — new `report` subcommand
- `pyproject.toml` — package-data for the template
- `tests/test_macos_platform.py`, `tests/test_report.py` — new
- `README.md`, `docs/CLI_USAGE_ZH.md` — backend/permission corrections, report
  usage

## Validation

```text
40 tests OK (21 baseline + 14 macOS platform + 5 report)
python -m compileall focusledger tests OK
static template review: all checks pass
end-to-end: init → insert samples → report --today → file parses, token
replaced, embedded JSON round-trips
```

## Open edges

- The subprocess backend's private-window tracking stays title-marker-only by
  platform limitation; full parity requires PyObjC (documented, warned at
  startup).
- Deep-work session thresholds (25 min / 5 min slack) are fixed in the
  template for now; if they earn their keep they should move into
  `AppConfig` alongside the attention thresholds.
- The report is per-day; a multi-day/trend view is a natural next step but out
  of scope here.
