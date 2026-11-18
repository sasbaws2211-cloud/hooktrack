CREATE TABLE IF NOT EXISTS post_studio_covers (
    post_id       INT PRIMARY KEY REFERENCES posts(id) ON DELETE CASCADE,
    workspace_id  INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_path     TEXT NOT NULL,
    file_name     TEXT NOT NULL DEFAULT '',
    file_size     BIGINT NOT NULL DEFAULT 0,
    content_type  TEXT NOT NULL DEFAULT 'image/png',
    title         TEXT NOT NULL DEFAULT '',
    logo_text     TEXT NOT NULL DEFAULT '',
    primary_color TEXT NOT NULL DEFAULT '#ffffff',
    accent_color  TEXT NOT NULL DEFAULT '#ffe15a',
    font_family   TEXT NOT NULL DEFAULT 'Roboto',
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS post_studio_covers_workspace_idx ON post_studio_covers (workspace_id);
