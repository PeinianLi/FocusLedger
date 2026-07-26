# Devlog: Schema v8 Integration

Updated: 2026-07-26
Author: Peinian Li

## Summary

This update integrates two lines of development into one database contract:

- the Phase 3-9 MVP features for workdays, classification, deliverables,
  distractions, reviews, and autostart;
- the raw/cooked data retention model, nightly archival, and durable time-zone
  context.

The resulting schema version is `8`. Existing FocusLedger data is preserved
while the new feature tables are added.

## Unified Database Model

The merged database has three storage groups:

- **raw data**: `raw_activity_events` and `agent_heartbeats`;
- **cooked data**: `activity_segments` and `attention_segments`;
- **user and feature state**: workday sessions and pauses, classification
  rules and corrections, deliverables, tasks, notes, distractions, and daily
  reviews.

Raw data remains reprocessable for a configurable number of system-local
calendar dates, seven by default. At the first agent run after a local date
change, expired complete dates are archived transactionally into cooked data
before their raw rows are removed.

Historical reports, timelines, attention summaries, classification, and
deliverable attribution can consume cooked data. Historical workday reviews
also treat cooked activity intervals as agent coverage after old heartbeats
have expired.

## Time-Zone Safety

New raw samples retain:

- the original local timestamp and normalized UTC timestamp;
- local date and local-midnight UTC;
- the system time-zone identifier and display name;
- UTC offset and daylight-saving state.

Ordering and duration calculations use UTC. A regression test covers the
repeated hour during the autumn DST transition and verifies that one continuous
foreground action is not split or truncated when the clock moves backward.

## Migration and Runtime Compatibility

The migration is idempotent and supports older databases by:

- creating all Phase 3-9 and cooked-data tables when missing;
- adding and backfilling recoverable time-context columns;
- preserving raw and cooked row counts;
- removing the redundant `daily_app_attention` table;
- recording schema version `8`.

The local report server now closes each per-request SQLite connection
explicitly. This prevents file locks from accumulating on Windows.

## Production Verification

Before the production migration, the schema-v7 database was copied through the
SQLite backup API and migrated as a temporary rehearsal. The rehearsal
preserved all four existing raw/cooked table counts and passed
`PRAGMA integrity_check`.

The production database was then backed up and migrated. Its existing row
counts were preserved, the integrity check returned `ok`, and the restarted
background agent reported a fresh non-stale heartbeat under schema v8.

The newest raw sample was verified to contain UTC, `Eastern Standard Time`,
`Eastern Daylight Time`, the `-04:00` offset, DST state, and local-midnight UTC.

## Validation

```bash
python -m unittest discover -s tests
python -m compileall -q focusledger tests
```

All 122 tests pass.
