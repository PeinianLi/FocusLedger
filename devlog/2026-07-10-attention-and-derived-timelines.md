# Devlog: Attention Timeline, Background Agent, and Derived Data Model

Updated: 2026-07-10
Author: Peinian Li
Baseline commit: `68889fe Add macOS collection (dependency-free), privacy/dev services, idle tracking + attention summary`

## Summary

This update turns FocusLedger's attention tracking into a more coherent, query-derived model. The agent still records raw foreground-window samples and keyboard/mouse idle seconds, but activity segments, attention states, attention weights, and attention timelines are now treated as derived views instead of durable database facts.

The work also restores and improves background agent operation, adds configurable Part 10 attention thresholds/weights, improves CLI reporting, and tightens privacy behavior for private browsing and unknown/system-idle windows.

## Conversation and Design Path

The update started with practical runtime problems: the agent could report a stale `running` heartbeat after a reboot, and starting it in the foreground made it awkward to inspect status from the same terminal. That led to restoring `start --background`, adding stale heartbeat reporting, and making Ctrl+C / stop behavior write a clean stopped heartbeat.

After that, Proposal Part 10 was implemented as a keyboard/mouse idle based attention detector. The first version stored attention state and attention segments directly. During logic testing, examples like the following shaped the model:

```text
13:01 heartbeat sees last input at 13:00
```

That exposed an important semantic question: should the system label only the current sample, or infer the interval since the last real input? The model first moved toward interval inference, so a sample could backfill:

```text
13:00 - 13:01 active
```

The model was then refined again after considering the case:

```text
13:00 last input
14:00 next input
```

The desired interpretation was not a gradual ladder of active -> low_activity -> gap -> interrupted -> hard_idle. Once the next real input confirms a long absence, the whole no-input interval should be treated as:

```text
13:00 - 14:00 hard_idle
```

That discussion pushed the model away from eagerly stored attention segments and toward recomputation from raw observations.

Finally, `attention_state`, `attention_weight`, `activity_segments`, and `attention_segments` were identified as derivable. Storing them creates consistency problems when rules change. The data model was updated so new databases store only raw samples and metadata; reports derive segments at read time.

## Behavioral Changes

### Background Agent

- Added `python -m focusledger start --background`.
- Background mode writes stdout/stderr logs under the configured data directory.
- `status` now reports heartbeat age and stale status.
- Restarting after reboot/start clears old stop requests.
- Foreground Ctrl+C writes a stopped heartbeat.

### Attention Policy

Added configurable Part 10 attention states:

```text
active
low_activity
attention_gap
interrupted
hard_idle
unknown
```

Default thresholds:

```text
0-3 min       active
3-10 min      low_activity
10-20 min     attention_gap
20-30 min     interrupted
30+ min       hard_idle
```

Default weights:

```text
active          1.0
low_activity    0.5
attention_gap   0.0
interrupted     0.0
hard_idle       0.0
```

New CLI commands:

```bash
python -m focusledger config attention
python -m focusledger config set attention-thresholds 3 10 20 30
python -m focusledger config set attention-weights 1.0 0.5 0.0 0.0 0.0
```

### Attention Reporting

`python -m focusledger attention --today` now shows:

- total time by attention state
- raw time
- effective weighted attention time
- per-app approximation
- a compact horizontal attention timeline

`--timeline` expands the detailed attention intervals:

```bash
python -m focusledger attention --today --timeline
python -m focusledger attention --date 2026-07-09 --timeline
```

The compact timeline now has spacing between sections and uses readable symbols. It automatically falls back to ASCII when the terminal encoding cannot print block characters.

Unicode-capable output:

```text
[▒░░░████████▒████████]
legend: █ active | ▒ low_activity | ░ attention_gap | ! interrupted | · hard_idle | ? unknown
```

ASCII fallback:

```text
[LGGGAAAAAAAALAAAAAAAA]
legend: A active | L low_activity | G attention_gap | ! interrupted | H hard_idle | ? unknown
```

## Data Model Change

New databases no longer create or write these derived structures:

```text
activity_segments
attention_segments
raw_activity_events.attention_state
raw_activity_events.attention_weight
```

The durable event stream is now centered on `raw_activity_events`:

```text
captured_at
process_name
window_title
executable_path
window_handle
source
idle_seconds
created_at
```

Reports derive their views at query time:

- `timeline` derives activity segments from adjacent raw samples.
- `summary` groups derived activity segments by app/process.
- `attention` derives attention intervals from idle history and the current policy.

This keeps old data reusable when attention rules change.

Schema version was bumped to `v4`. Existing databases are not destructively rewritten; old derived tables/columns may remain if already present, but new code no longer writes them.

## Timeline Fixes

### Unknown Process Merging

Consecutive `(unknown)` samples were found not to merge:

```text
21:35:05 - 21:35:05  0s  (unknown)
21:35:06 - 21:35:06  0s  (unknown)
...
```

The cause was normalization mismatch: raw rows used `None`, while displayed segments used `"(unknown)"`. The derived activity key now normalizes both sides consistently:

```text
None process_name -> (unknown)
None/empty title  -> ""
```

So continuous unknown/system-idle windows now merge into a single segment.

### Screen Saver Interpretation

The user observed that `(unknown)` often corresponds to the Windows screen saver. The current implementation keeps the raw process as unknown, while attention reports use idle history to classify the period. This avoids guessing at storage time while still making the timeline useful.

## Privacy Updates

- Private/incognito browser titles are still redacted to `[private browsing]`.
- Chinese private-window title markers are recognized.
- The private-window cache key now includes process/window identity to avoid over-redacting unrelated browser windows.
- The privacy redaction helper remains compatible with old databases that still contain `activity_segments`.

## Files Touched

Main implementation areas:

- `focusledger/agent/runner.py`
- `focusledger/cli.py`
- `focusledger/config.py`
- `focusledger/db/migrations.py`
- `focusledger/db/schema.sql`
- `focusledger/services/activity.py`
- `focusledger/services/attention.py`
- `focusledger/services/privacy.py`

Documentation and tests:

- `README.md`
- `tests/test_activity_segments.py`
- `tests/test_attention.py`
- `tests/test_migrations_and_cli.py`

## Validation

Current verification:

```bash
python -m unittest discover -s tests
python -m compileall focusledger tests
```

Latest result:

```text
21 tests OK
```

## Notes and Open Edges

- Per-app attention is still an approximation because app ownership is sampled on raw events, while attention intervals can span across app changes.
- Part 11 audio/video correction is not implemented yet.
- Existing old databases may still physically contain derived tables or columns, but they are treated as legacy compatibility data.
- Linux collection remains reserved for a later phase.
