-- One row per Shotstack render job (the video/image editor's cloud-render path). post_id is nullable because a
-- render can be submitted before it's known which card it's for (or standalone); the attach-to-post step is a
-- separate update once the render finishes.
CREATE TABLE IF NOT EXISTS shotstack_renders (
    id            SERIAL PRIMARY KEY,
    workspace_id  INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id       INT REFERENCES posts(id) ON DELETE SET NULL,
    render_id     TEXT NOT NULL UNIQUE,          -- Shotstack's own render job id
    environment   TEXT NOT NULL DEFAULT 'stage', -- 'stage' (sandbox) | 'v1' (production)
    edit_json     JSONB NOT NULL,                -- what we submitted, kept for retries/debugging
    status        TEXT NOT NULL DEFAULT 'queued',-- queued|fetching|preprocessing|generating|rendering|saving|done|failed
    output_url    TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shotstack_renders_ws_idx ON shotstack_renders (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS shotstack_renders_post_idx ON shotstack_renders (post_id);
