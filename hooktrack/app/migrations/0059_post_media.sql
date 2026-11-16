-- Phase 7: a post's images (one for a photo post, several for a carousel), as an ordered list - the existing
-- video_file/video_url columns on posts stay exactly as they are and are untouched by this; a video post keeps
-- using them. Carousels/photos never had a storage model at all before this.
CREATE TABLE IF NOT EXISTS post_media (
    id            SERIAL PRIMARY KEY,
    workspace_id  INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id       INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    position      INT NOT NULL DEFAULT 0,
    file_path     TEXT NOT NULL,                    -- relative under uploads/, same convention as posts.video_file
    file_name     TEXT NOT NULL DEFAULT '',
    file_size     BIGINT NOT NULL DEFAULT 0,
    content_type  TEXT NOT NULL DEFAULT '',          -- image/jpeg, image/png, image/webp
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS post_media_post_idx ON post_media (post_id, position);
