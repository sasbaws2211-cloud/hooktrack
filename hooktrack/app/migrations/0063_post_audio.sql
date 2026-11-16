-- Audio files added in the Studio editor (voiceover, background music) - same shape as post_media, but
-- there's no equivalent concept for audio elsewhere in the app (publishing providers don't take audio
-- separately), so this only ever gets read back by Studio itself to re-seed a draft's audio clips.
CREATE TABLE IF NOT EXISTS post_audio (
    id            SERIAL PRIMARY KEY,
    workspace_id  INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id       INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    position      INT NOT NULL DEFAULT 0,
    file_path     TEXT NOT NULL,
    file_name     TEXT NOT NULL DEFAULT '',
    file_size     BIGINT NOT NULL DEFAULT 0,
    content_type  TEXT NOT NULL DEFAULT 'audio/mpeg',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS post_audio_post_idx ON post_audio (post_id, position);
