-- 002: persistent upload scheduler, YouTube connection, encrypted secret store, new status model.
-- Existing videos and channel settings are preserved; old status values are mapped to the new ones.

ALTER TABLE videos ADD COLUMN scheduled_for TEXT;          -- UTC ISO time of the publishing slot
ALTER TABLE videos ADD COLUMN slot_id INTEGER;
ALTER TABLE videos ADD COLUMN slot_date TEXT;              -- slot date in the scheduler timezone (YYYY-MM-DD)
ALTER TABLE videos ADD COLUMN upload_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE videos ADD COLUMN next_retry_at TEXT;          -- UTC ISO; retry not before this time
ALTER TABLE videos ADD COLUMN target_privacy TEXT;         -- private | unlisted | public
ALTER TABLE videos ADD COLUMN youtube_url TEXT;
ALTER TABLE videos ADD COLUMN approved_at TEXT;
ALTER TABLE videos ADD COLUMN published_at TEXT;
ALTER TABLE videos ADD COLUMN rescheduled INTEGER NOT NULL DEFAULT 0;

-- one video per slot occurrence; one local video per YouTube id (duplicate-publishing guard)
CREATE UNIQUE INDEX IF NOT EXISTS ux_videos_slot ON videos(slot_id, slot_date) WHERE slot_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_videos_ytid ON videos(youtube_video_id) WHERE youtube_video_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_videos_sched ON videos(scheduled_for);

UPDATE videos SET status = CASE status
    WHEN 'queued' THEN 'DRAFT'
    WHEN 'retry' THEN 'DRAFT'
    WHEN 'running' THEN 'GENERATING'
    WHEN 'needs_review' THEN 'WAITING_FOR_APPROVAL'
    WHEN 'ready' THEN 'READY'
    WHEN 'uploading' THEN 'FAILED'
    WHEN 'uploaded' THEN 'PUBLISHED'
    WHEN 'failed' THEN 'FAILED'
    WHEN 'paused' THEN 'PAUSED'
    ELSE status END;
UPDATE videos SET youtube_url = 'https://youtu.be/' || youtube_video_id WHERE youtube_video_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS schedule_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('short','long')),
    time_local TEXT NOT NULL,                              -- HH:MM in the scheduler timezone
    days TEXT NOT NULL DEFAULT 'mon,tue,wed,thu,fri,sat,sun',
    enabled INTEGER NOT NULL DEFAULT 1,
    paused INTEGER NOT NULL DEFAULT 0,
    channel_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO schedule_slots(label, kind, time_local) VALUES
    ('Short 1', 'short', '09:00'),
    ('Short 2', 'short', '13:00'),
    ('Short 3', 'short', '18:00'),
    ('Long video', 'long', '20:30');

CREATE TABLE IF NOT EXISTS youtube_connection (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'disconnected',            -- connected | reauth_required | disconnected
    channel_id TEXT,
    channel_title TEXT,
    channel_thumbnail TEXT,
    scopes TEXT,
    connected_at TEXT,
    last_refresh_at TEXT,
    token_expiry TEXT,
    last_error TEXT
);
INSERT OR IGNORE INTO youtube_connection(id, status) VALUES (1, 'disconnected');

CREATE TABLE IF NOT EXISTS oauth_states (
    state TEXT PRIMARY KEY,
    verifier TEXT NOT NULL,                                 -- encrypted PKCE verifier
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS secure_store (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,                                    -- base64 of DPAPI/Fernet-encrypted JSON
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
