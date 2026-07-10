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

CREATE TABLE IF NOT EXISTS raw_activity_events (
    id TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    process_id INTEGER,
    process_name TEXT,
    executable_path TEXT,
    window_title TEXT,
    window_handle TEXT,
    source TEXT NOT NULL,
    idle_seconds REAL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_activity_events_captured_at
ON raw_activity_events(captured_at);

CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    process_name TEXT NOT NULL,
    executable_path TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(process_name, executable_path)
);
