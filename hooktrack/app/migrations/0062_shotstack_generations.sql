-- One row per Shotstack asset-generation job (the Studio editor's "Generate" toolbar - a single prompt-bearing
-- image/video/audio asset, not a full render). post_id is nullable for the same reason as shotstack_renders:
-- generation can happen before "Finish" ever runs, while the user is still editing.
CREATE TABLE IF NOT EXISTS shotstack_generations (
    id             SERIAL PRIMARY KEY,
    workspace_id   INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id        INT REFERENCES posts(id) ON DELETE SET NULL,
    generation_id  TEXT NOT NULL UNIQUE,           -- Shotstack's own generation job id
    environment    TEXT NOT NULL DEFAULT 'stage',
    asset_json     JSONB NOT NULL,                 -- the {type, prompt, model, options} we submitted
    status         TEXT NOT NULL DEFAULT 'queued',  -- queued|processing|done|failed
    output_url     TEXT NOT NULL DEFAULT '',
    error          TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS shotstack_generations_ws_idx ON shotstack_generations (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS shotstack_generations_post_idx ON shotstack_generations (post_id);
