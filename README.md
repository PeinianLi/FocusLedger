# FocusLedger

FocusLedger is a local-first personal productivity audit tool. The first
engineering milestone focuses on a trustworthy Windows data chain:

```text
agent start
-> active window sampling
-> SQLite persistence
-> heartbeat/status
-> timeline and app summary queries
```

## Quick Start

```bash
python -m focusledger init
python -m focusledger start --once
python -m focusledger status
python -m focusledger timeline --today
python -m focusledger summary --today
```

Use `FOCUSLEDGER_DATA_DIR` to override the default local data directory.
