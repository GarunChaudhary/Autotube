-- 004: story genre per video + "upload immediately" mode (upload as soon as production and checks finish).
ALTER TABLE videos ADD COLUMN genre TEXT;
ALTER TABLE videos ADD COLUMN upload_immediately INTEGER NOT NULL DEFAULT 0;
ALTER TABLE videos ADD COLUMN skip_review INTEGER NOT NULL DEFAULT 0;
