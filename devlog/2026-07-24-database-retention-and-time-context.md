# Devlog: Database Retention, Nightly Archival, and Time Context

Updated: 2026-07-24
Author: Peinian Li

## Summary

This update changes FocusLedger from an indefinitely growing raw-sample
database into a two-tier local data store:

- recent data remains available as raw, reprocessable samples;
- older data is retained as compact activity and attention segments.

Raw events and high-frequency heartbeats now use a configurable retention
window of seven local calendar dates by default. The background agent archives
expired complete days after the operating system's local date changes. If the
agent is not running at midnight, it catches up on its next start.

The update also makes stored and derived timestamps safe across operating
system time-zone changes and daylight-saving transitions.

## Retention Model

The new configuration field is:

```text
raw_retention_days = 7
```

Seven means today plus the previous six system-local dates. A date outside
that window is processed in a transaction:

```text
read one complete raw date
-> derive activity timeline
-> derive attention timeline
-> write the archived rows
-> delete that date's raw events
-> commit
```

If any step fails, the date rolls back as a unit. A partially written archive
can therefore never replace raw data.

The agent runs retention once at startup and again whenever the operating
system's local date changes. Failures are retried after five minutes.

Manual maintenance commands were added:

```bash
python -m focusledger db prune
python -m focusledger db compact --yes
python -m focusledger config retention
python -m focusledger config set raw-retention-days 14
```

## Durable Archive Tables

Two compact durable tables preserve old data after raw samples expire:

- `activity_segments`: foreground application and window-title intervals;
- `attention_segments`: continuous attention-state intervals;

Recent-date queries continue deriving results from `raw_activity_events`.
Older-date queries automatically fall back to the archive tables, so the
existing timeline, summary, attention, and report commands keep the same user
interface. Per-application attention is calculated when queried by intersecting
the activity and attention intervals in normalized UTC; it is not stored as a
third copy.

## Time-Zone and DST Safety

The original `captured_at` value already included a UTC offset, which preserved
the absolute instant of an individual sample. It was not sufficient for every
timeline operation: lexicographic local-time ordering can reverse the repeated
hour during the autumn DST transition, and wall-clock chart coordinates can
fold back on themselves.

New raw samples therefore store:

```text
captured_at
captured_at_utc
local_date
timezone_id
timezone_name
utc_offset_seconds
is_dst
local_day_start_utc
```

Archived activity and attention intervals retain the corresponding UTC and
time-zone context. Ordering and duration calculations use normalized UTC.
Reports map those absolute times back onto the captured local-day context.

On Windows, the durable time-zone identifier comes from the system registry
(for example, `Eastern Standard Time`), while the current display name records
whether the sample was captured under standard or daylight time.

A regression test covers the autumn transition:

```text
01:59:59 -04:00
01:00:00 -05:00
```

The second record now remains later on the timeline instead of sorting before
the first record or truncating the chart.

The persisted config no longer contains a fixed time zone. Date boundaries and
nightly scheduling follow the operating system's current local time.

## Schema and Storage

The integrated schema version is `8`. Schema v8 combines the retention and
time-context design with the Phase 3-9 tables added by the concurrently
developed MVP work: workday sessions, pauses, classification rules, manual
corrections, deliverables, tasks, notes, distractions, and daily reviews.
Schema v7 removed the redundant `daily_app_attention` table; its values are
derivable from the two cooked segment tables and a stored copy could drift out
of sync.

The initial production migration:

- reduced raw activity events from roughly 2.71 million rows to roughly
  490 thousand rows;
- reduced agent heartbeats from roughly 2.71 million rows to roughly
  490 thousand rows;
- compacted the SQLite file from 1,442,193,408 bytes to 253,841,408 bytes;
- released 1,188,352,000 bytes of disk space;
- enabled incremental auto-vacuum for future nightly retention.

Existing timestamps were backfilled with normalized UTC values and local
dates where those facts could be recovered. Historical time-zone identity and
DST metadata remain null when they cannot be reconstructed reliably.

## One-Month Storage Study

This study uses the agreed storage terminology:

- **raw data**: `raw_activity_events` plus
  `agent_heartbeats`;
- **cooked data**: `activity_segments` plus
  `attention_segments`;
- **other data**: the application catalog, settings, metadata, SQLite layout
  overhead, and the WAL/SHM sidecar files.

### Method and assumptions

The measurement was taken on 2026-07-24 from the production database. Each
category was copied into an isolated SQLite database with the production table
definitions, indexes, 4 KiB pages, and incremental auto-vacuum, then compacted
before its file size was measured. This includes real SQLite row, primary-key,
index, and page overhead rather than only adding the lengths of column values.

The production database at measurement time contained:

| Table | Rows |
|---|---:|
| `raw_activity_events` | 496,776 |
| `agent_heartbeats` | 1,147 |
| `activity_segments` | 11,738 |
| `attention_segments` | 541 |
| `applications` | 77 |
| `settings` | 1 |
| `app_metadata` | 2 |

The main file was 214,487,040 bytes, with a 4,128,272-byte WAL and a
32,768-byte SHM file. That current size is not itself the one-month answer:
historical heartbeat rows had previously been compacted during the rolled-back
heartbeat experiment, and only 3,908 current raw rows had complete time-zone
context. The forecast therefore uses:

- one activity sample and one heartbeat per second;
- a computer and agent that remain awake continuously for 30 local dates;
- the configured seven-date raw retention window;
- the observed distribution of process names, paths, and window-title lengths;
- complete time-zone and DST context on all future rows;
- cooked-segment rates from the nine complete days 2026-07-06 through
  2026-07-14.

Those nine days averaged 446 Timeline rows and 51.3 Attention rows per day.
Observed daily cooked-data production ranged from roughly 0.145 MB to
0.814 MB as foreground-window switching and attention-state changes varied.

### Measured unit costs

The isolated databases produced these effective costs, including indexes:

| Record type | Effective bytes per row |
|---|---:|
| Raw activity event with full time context | 487.57 |
| Agent heartbeat | 191.02 |
| Cooked Timeline segment with full time context | 683.25 |
| Cooked Attention segment with full time context | 635.98 |

The heartbeat value was measured from a generated seven-day database of
604,800 rows with the same field lengths and indexes as production. Hydrated
copies of the cooked tables were used because all historical cooked rows
predated complete time-zone context and would otherwise underestimate future
storage.

### Thirty-day forecast

At the end of day 30, the high-water case contains seven complete raw dates
(604,800 rows in each raw table) and 23 dates represented by cooked data:

| Category | Estimated storage | Share of on-disk high water |
|---|---:|---:|
| Raw activity events | 294.9 MB | 68.8% |
| Raw agent heartbeats | 115.5 MB | 27.0% |
| Cooked Timeline segments | 7.0 MB | 1.6% |
| Cooked Attention segments | 0.75 MB | 0.2% |
| Other tables, layout overhead, WAL, and SHM | 10.4 MB | 2.4% |
| **Total** | **428.6 MB** | **100%** |

Here MB means decimal megabytes. The same total is approximately 408.8 MiB,
or 0.40 GiB. A practical planning figure is therefore **0.42-0.43 GB at the
daily high-water point** for uninterrupted one-second sampling.

Inside the compact main database alone, before observed page-layout overhead
and WAL/SHM are added, raw data accounts for 98.13%, cooked data for 1.86%,
and static tables for less than 0.02%. Including the measured 1.49% main-file
layout difference and the roughly 4.16 MB sidecar files produces the on-disk
shares in the table above: 95.75% raw, 1.81% cooked, and 2.44% other.

### Growth behavior

For the first seven days, before the retention window is full:

```text
raw activity growth   ~= 42.13 MB/day
heartbeat growth      ~= 16.50 MB/day
combined raw growth   ~= 58.63 MB/day
```

From day eight onward, each nightly run archives and removes one old raw date.
The live raw-data allocation therefore becomes a seven-date ring instead of
continuing to grow with elapsed months. Average net long-term growth is then
the cooked archive:

```text
Timeline growth       ~= 0.305 MB/day
Attention growth      ~= 0.033 MB/day
combined cooked growth ~= 0.337 MB/day
```

At that observed rate, cooked data adds about 10.1 MB per further 30 days, or
123 MB per year. A month with unusually frequent window switching could be
closer to the measured upper bound of 24.4 MB of new cooked data; a quiet month
could be near 4.3 MB.

Because retention follows local calendar dates, storage has a daily sawtooth:
immediately after midnight archival there are six full retained dates plus a
nearly empty current date, while immediately before the next archival there
are almost seven full dates. The modeled total ranges from about 369 MB just
after archival to 429 MB just before it, with a midpoint near 399 MB. SQLite
may retain and reuse some high-water pages between those points, so the
physical file need not visibly shrink by the full amount every night.

Every additional configured raw-retention date costs approximately 58.6 MB at
the current one-second frequency and data shape. A longer polling interval
reduces both raw tables approximately in inverse proportion. System sleep,
agent downtime, shorter titles, or fewer foreground changes reduce the actual
result; unusually long titles and paths increase it. The forecast is therefore
an empirical capacity model, not a fixed upper bound.

## Configuration Decision

Static settings may later support environment-variable or `.env` overrides,
but no environment-file change was made in this update. `config.json` remains
the durable user configuration, while the SQLite `settings` table continues
to hold the runtime stop request used for CLI-to-agent coordination.

## Validation

Validation completed with:

```bash
python -m unittest discover -s tests
python -m compileall focusledger tests
```

All 122 tests pass after integrating the Phase 3-9 branch. Coverage includes:

- default and configurable retention windows;
- idempotent archival;
- archive-before-delete behavior;
- old-schema migration;
- old-date query fallback;
- per-app attention calculation from cooked interval overlap;
- system-local date scheduling;
- normalized UTC ordering;
- daylight-saving repeated-hour behavior.

The production database was backed up, migrated to schema v8, and passed
`PRAGMA integrity_check`. The restarted agent was then verified to write the
time context, including UTC, Windows time-zone ID, UTC offset, DST state, and
local-midnight UTC.
