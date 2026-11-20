-- 001_init: core schema
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    niche TEXT NOT NULL DEFAULT '',
    audience TEXT NOT NULL DEFAULT '',
    tone TEXT NOT NULL DEFAULT 'curious, warm, energetic',
    language TEXT NOT NULL DEFAULT 'en',
    youtube_channel_id TEXT,
    youtube_title TEXT,
    shorts_per_day INTEGER NOT NULL DEFAULT 3,
    longs_per_day INTEGER NOT NULL DEFAULT 1,
    long_minutes INTEGER NOT NULL DEFAULT 6,
    privacy TEXT NOT NULL DEFAULT 'private',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id),
    kind TEXT NOT NULL CHECK (kind IN ('short','long')),
    topic TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    stage TEXT NOT NULL DEFAULT 'research',
    attempts INTEGER NOT NULL DEFAULT 0,
    research_json TEXT,
    script_json TEXT,
    factcheck_json TEXT,
    storyboard_json TEXT,
    seo_json TEXT,
    quality_json TEXT,
    title TEXT,
    description TEXT,
    tags TEXT,
    video_path TEXT,
    thumbnail_path TEXT,
    subtitle_path TEXT,
    duration_sec REAL,
    youtube_video_id TEXT,
    privacy TEXT,
    publish_at TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_topic_key ON videos(channel_id, topic_key);

CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL REFERENCES videos(id),
    youtube_video_id TEXT,
    status TEXT NOT NULL,
    privacy TEXT,
    publish_at TEXT,
    response_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS quota_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    day TEXT NOT NULL,
    operation TEXT NOT NULL,
    units INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_quota_day ON quota_usage(provider, day);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    source TEXT NOT NULL,
    message TEXT NOT NULL,
    video_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    youtube_video_id TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_reports (
    day TEXT PRIMARY KEY,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
