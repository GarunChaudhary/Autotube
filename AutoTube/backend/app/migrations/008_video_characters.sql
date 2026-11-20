-- 008: videos made with AI characters. character_ids = JSON list of ai_characters ids (speaking order),
-- character_mode = narrator | podcast (talking_face / dance need a GPU and are not accepted yet).
ALTER TABLE videos ADD COLUMN character_ids TEXT;
ALTER TABLE videos ADD COLUMN character_mode TEXT;
