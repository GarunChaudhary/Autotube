-- 006: growth engine. One engagement comment per public video (posted once, tracked here).
ALTER TABLE videos ADD COLUMN engagement_comment_id TEXT;
ALTER TABLE videos ADD COLUMN engagement_comment_attempts INTEGER NOT NULL DEFAULT 0;
