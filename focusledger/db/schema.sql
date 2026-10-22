CREATE TABLE IF NOT EXISTS app_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_heartbeats (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    pid INTEGER,
    status TEXT NOT NULL,
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_heartbeats_timestamp
ON agent_heartbeats(timestamp);

-- One row per sample, written every poll, so the row stays narrow: the
-- application and the time zone context repeat on nearly every sample and are
-- stored once in lookup tables. Local capture time is rebuilt from UTC plus
-- the context's offset. Indexes live in migrations.py because older databases
-- still have the pre-v9 shape when this script runs.
CREATE TABLE IF NOT EXISTS raw_activity_events (
    id INTEGER PRIMARY KEY,
    device_id TEXT NOT NULL,
    captured_at_utc TEXT NOT NULL,
    local_date TEXT NOT NULL,
    time_context_id INTEGER REFERENCES time_contexts(id),
    process_id INTEGER,
    application_id INTEGER REFERENCES applications(id),
    window_title TEXT,
    window_handle TEXT,
    source TEXT NOT NULL,
    idle_seconds REAL
);

CREATE TABLE IF NOT EXISTS time_contexts (
    id INTEGER PRIMARY KEY,
    timezone_id TEXT,
    timezone_name TEXT,
    utc_offset_seconds INTEGER,
    is_dst INTEGER,
    local_day_start_utc TEXT
);

CREATE INDEX IF NOT EXISTS idx_time_contexts_day_start
ON time_contexts(local_day_start_utc);

CREATE TABLE IF NOT EXISTS activity_segments (
    id TEXT PRIMARY KEY,
    local_date TEXT,
    device_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    started_at_utc TEXT,
    ended_at_utc TEXT,
    timezone_id TEXT,
    timezone_name TEXT,
    utc_offset_seconds INTEGER,
    is_dst INTEGER,
    local_day_start_utc TEXT,
    duration_seconds REAL NOT NULL,
    process_name TEXT,
    window_title TEXT,
    executable_path TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attention_segments (
    id TEXT PRIMARY KEY,
    local_date TEXT,
    device_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    started_at_utc TEXT,
    ended_at_utc TEXT,
    timezone_id TEXT,
    timezone_name TEXT,
    utc_offset_seconds INTEGER,
    is_dst INTEGER,
    local_day_start_utc TEXT,
    duration_seconds REAL NOT NULL,
    attention_state TEXT NOT NULL,
    attention_weight REAL NOT NULL,
    max_idle_seconds REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY,
    process_name TEXT,
    executable_path TEXT,
    first_seen_at TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Phase 3-8 tables.
--
-- Only facts and user input are stored here. Anything that can be recomputed
-- from raw events (classifications, missing-data intervals, category totals,
-- deliverable time attribution) stays derived at query time, so changing a
-- rule or threshold re-explains history instead of leaving stale rows behind.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS workday_sessions (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    workday_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    timezone TEXT,
    status TEXT NOT NULL,
    is_overtime INTEGER NOT NULL DEFAULT 0,
    offwork_target_time TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Samples belong to the device that captured them; a workday and everything
-- the user writes about it (notes, review) is one per day across devices, so
-- device_id on a session only records where it was started.
CREATE UNIQUE INDEX IF NOT EXISTS idx_workday_sessions_workday_key
    ON workday_sessions(workday_key);

CREATE TABLE IF NOT EXISTS workday_pauses (
    id TEXT PRIMARY KEY,
    workday_id TEXT NOT NULL REFERENCES workday_sessions(id),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_workday_pauses_workday
    ON workday_pauses(workday_id, started_at);

CREATE TABLE IF NOT EXISTS classification_rules (
    id TEXT PRIMARY KEY,
    priority INTEGER NOT NULL,
    match_type TEXT NOT NULL,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_classification_rules_target
    ON classification_rules(match_type, pattern);

CREATE TABLE IF NOT EXISTS manual_corrections (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    pattern TEXT NOT NULL,
    category TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_manual_corrections_target
    ON manual_corrections(scope, pattern);

CREATE TABLE IF NOT EXISTS deliverables (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    priority INTEGER NOT NULL DEFAULT 2,
    due_date TEXT,
    match_keywords TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT REFERENCES deliverables(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    estimate_minutes INTEGER,
    blocker TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_deliverable ON tasks(deliverable_id);

CREATE TABLE IF NOT EXISTS daily_notes (
    id TEXT PRIMARY KEY,
    workday_key TEXT NOT NULL,
    progress TEXT,
    blocked TEXT,
    tomorrow TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_notes_key ON daily_notes(workday_key);

CREATE TABLE IF NOT EXISTS distraction_events (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    workday_key TEXT,
    detected_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    category TEXT,
    process_name TEXT,
    duration_seconds REAL,
    reason TEXT,
    labeled_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_distraction_events_day
    ON distraction_events(workday_key, detected_at);

CREATE TABLE IF NOT EXISTS daily_reviews (
    id TEXT PRIMARY KEY,
    workday_key TEXT NOT NULL,
    markdown TEXT NOT NULL,
    generator TEXT NOT NULL,
    llm_status TEXT NOT NULL,
    llm_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_daily_reviews_key ON daily_reviews(workday_key);

-- Stretches the user says they were away from the computer (asleep, out,
-- eating). A user fact, applied when segments are read: raw samples are never
-- rewritten, so deleting the row restores the original reading.
CREATE TABLE IF NOT EXISTS time_annotations (
    id TEXT PRIMARY KEY,
    local_date TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT NOT NULL,
    label TEXT NOT NULL,
    note TEXT,
    change_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_time_annotations_range
    ON time_annotations(started_at_utc, ended_at_utc);

-- Every change written on the user's behalf (approved analyst proposals),
-- with what it takes to undo it.
CREATE TABLE IF NOT EXISTS change_log (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL,
    action TEXT NOT NULL,
    arguments TEXT NOT NULL,
    summary TEXT NOT NULL,
    undo TEXT NOT NULL,
    undone_at TEXT
);
