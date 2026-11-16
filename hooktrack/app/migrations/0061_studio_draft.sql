-- The Studio editor's own in-progress work, so a user can leave the editor mid-edit (crops, filters, clip
-- order, added media) and pick back up later instead of losing it. Separate from the post's real
-- video/images, which only change once the user presses "Finish" - a draft never affects publishing.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS studio_draft JSONB;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS studio_draft_at TIMESTAMPTZ;
