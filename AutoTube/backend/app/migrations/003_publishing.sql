-- 003: public publishing, confirmed YouTube status tracking, thumbnail result tracking.
ALTER TABLE videos ADD COLUMN yt_privacy TEXT;            -- privacyStatus as last CONFIRMED by YouTube (videos.list)
ALTER TABLE videos ADD COLUMN yt_publish_at TEXT;         -- publishAt as confirmed by YouTube
ALTER TABLE videos ADD COLUMN yt_upload_status TEXT;      -- uploaded | processed | rejected | failed ...
ALTER TABLE videos ADD COLUMN yt_checked_at TEXT;
ALTER TABLE videos ADD COLUMN thumbnail_status TEXT;      -- set | unavailable | failed | invalid
ALTER TABLE videos ADD COLUMN thumbnail_error TEXT;       -- exact Google API reason/message

UPDATE videos SET yt_privacy = privacy WHERE youtube_video_id IS NOT NULL;
UPDATE videos SET thumbnail_status = 'unavailable',
                  thumbnail_error = 'HTTP 403 (exact reason was not recorded by the previous version - use Retry thumbnail)'
    WHERE youtube_video_id IS NOT NULL AND error LIKE '%thumbnail not set%';
UPDATE videos SET error = NULL WHERE youtube_video_id IS NOT NULL AND error LIKE '%thumbnail not set%';
-- new default: public. Videos not yet uploaded that were only private by the old default become public.
UPDATE videos SET target_privacy = 'public'
    WHERE youtube_video_id IS NULL AND (target_privacy IS NULL OR target_privacy = 'private');
