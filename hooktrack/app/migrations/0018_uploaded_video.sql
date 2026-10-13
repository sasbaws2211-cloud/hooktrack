-- A post's video can be a file the owner uploaded, as well as one a provider generated (posts.video_url).
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS video_file       TEXT        NOT NULL DEFAULT '',     -- path under uploads/, never shown to the browser
    ADD COLUMN IF NOT EXISTS video_file_name  TEXT        NOT NULL DEFAULT '',     -- the original file name, for display
    ADD COLUMN IF NOT EXISTS video_file_size  BIGINT      NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS video_file_type  TEXT        NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS video_file_at    TIMESTAMPTZ;
