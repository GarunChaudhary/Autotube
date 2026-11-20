-- 005: several YouTube accounts. Each channel profile (channels row) links to its own YouTube account.
-- The authorization (refresh token) is stored encrypted per profile in secure_store under 'youtube_token:<channel id>'.
CREATE TABLE youtube_accounts (
    channel_id INTEGER PRIMARY KEY REFERENCES channels(id),
    status TEXT NOT NULL DEFAULT 'disconnected',            -- connected | reauth_required | disconnected
    yt_channel_id TEXT,
    yt_channel_title TEXT,
    yt_channel_thumbnail TEXT,
    scopes TEXT,
    connected_at TEXT,
    last_refresh_at TEXT,
    token_expiry TEXT,
    last_error TEXT
);
-- one YouTube channel can be linked to only one profile (prevents double uploads to the same channel)
CREATE UNIQUE INDEX ux_youtube_accounts_yt ON youtube_accounts(yt_channel_id) WHERE yt_channel_id IS NOT NULL;

-- the existing single connection belongs to the first channel profile
INSERT INTO youtube_accounts(channel_id, status, yt_channel_id, yt_channel_title, yt_channel_thumbnail, scopes,
                             connected_at, last_refresh_at, token_expiry, last_error)
SELECT (SELECT MIN(id) FROM channels), status, channel_id, channel_title, channel_thumbnail, scopes,
       connected_at, last_refresh_at, token_expiry, last_error
FROM youtube_connection WHERE id = 1 AND (SELECT MIN(id) FROM channels) IS NOT NULL;

-- which profile an in-progress Google sign-in belongs to
ALTER TABLE oauth_states ADD COLUMN channel_id INTEGER;
