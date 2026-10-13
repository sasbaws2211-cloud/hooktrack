-- The exact winning video as a baseline: what was said, how it was built, and the clones made from it.
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS transcript     TEXT NOT NULL DEFAULT '',          -- raw WebVTT / plain text from the platform
    ADD COLUMN IF NOT EXISTS transcript_at  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS spoken_hook    TEXT NOT NULL DEFAULT '',          -- the first seconds of speech
    ADD COLUMN IF NOT EXISTS sound          TEXT NOT NULL DEFAULT '',          -- the sound/music used, when the platform tells us
    ADD COLUMN IF NOT EXISTS blueprint      JSONB,                             -- structure, signals, what to keep / vary
    ADD COLUMN IF NOT EXISTS video_notes    TEXT NOT NULL DEFAULT '',          -- the owner's own note about what is in the video
    ADD COLUMN IF NOT EXISTS cloned_from    INTEGER REFERENCES posts(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS posts_cloned_from_idx ON posts (cloned_from) WHERE cloned_from IS NOT NULL;
