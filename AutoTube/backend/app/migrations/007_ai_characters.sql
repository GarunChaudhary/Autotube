-- 007: AI characters (original AI-made people for podcast / dance / story videos) and their categories.
CREATE TABLE IF NOT EXISTS character_categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    icon        TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    builtin     INTEGER NOT NULL DEFAULT 0,
    sort        INTEGER NOT NULL DEFAULT 100,
    created_at  TEXT DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO character_categories(name, icon, description, builtin, sort) VALUES
    ('Podcast',      '🎙️', 'Two or more characters talking on a topic, with lip-synced faces.', 1, 10),
    ('Dance',        '💃', 'A character performing a dance from a motion video you own, on original music.', 1, 20),
    ('Storytelling', '📖', 'One character narrating a story or true event to camera.', 1, 30),
    ('Comedy skit',  '😂', 'Short scripted scenes between characters.', 1, 40),
    ('Motivation',   '🔥', 'A speaker character delivering a motivational talk.', 1, 50),
    ('Explainer',    '🧠', 'A host character explaining facts, science or history.', 1, 60);

CREATE TABLE IF NOT EXISTS ai_characters (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    category_id       INTEGER REFERENCES character_categories(id) ON DELETE SET NULL,
    channel_id        INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    gender            TEXT NOT NULL DEFAULT 'female',
    age_range         TEXT NOT NULL DEFAULT '20-30',
    style             TEXT NOT NULL DEFAULT 'realistic',
    appearance        TEXT NOT NULL DEFAULT '',
    personality       TEXT NOT NULL DEFAULT '',
    voice             TEXT NOT NULL DEFAULT 'af_heart',
    original_confirmed INTEGER NOT NULL DEFAULT 0,
    enabled           INTEGER NOT NULL DEFAULT 1,
    status            TEXT NOT NULL DEFAULT 'design',
    created_at        TEXT DEFAULT (datetime('now')),
    updated_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ai_characters_category ON ai_characters(category_id);
