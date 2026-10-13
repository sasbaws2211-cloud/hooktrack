-- A post the user made (Filmed / Posted card) gets its real results by link: `url` is the address of the posted
-- video, and result_post_id points at the synced post that address resolves to once the sync has pulled it in.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS result_post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS posts_result_post_idx ON posts (result_post_id) WHERE result_post_id IS NOT NULL;
