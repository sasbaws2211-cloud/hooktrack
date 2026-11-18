CREATE TABLE IF NOT EXISTS wavespeed_generations (
    id             SERIAL PRIMARY KEY,
    workspace_id   INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id        INT REFERENCES posts(id) ON DELETE SET NULL,
    prediction_id  TEXT NOT NULL UNIQUE,
    model          TEXT NOT NULL,
    asset_type     TEXT NOT NULL CHECK (asset_type IN ('image', 'video')),
    prompt         TEXT NOT NULL DEFAULT '',
    status         TEXT NOT NULL DEFAULT 'created',
    output_url     TEXT NOT NULL DEFAULT '',
    error          TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wavespeed_generations_ws_idx
    ON wavespeed_generations (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS wavespeed_generations_post_idx ON wavespeed_generations (post_id);
