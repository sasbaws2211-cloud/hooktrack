CREATE TABLE ad_boosts (
    id              SERIAL PRIMARY KEY,
    workspace_id    INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id          INT REFERENCES posts(id) ON DELETE SET NULL,
    post_label       TEXT NOT NULL DEFAULT '',
    platform         TEXT NOT NULL CHECK (platform IN ('meta', 'tiktok')),
    ad_account_id    TEXT NOT NULL,
    campaign_name    TEXT NOT NULL,
    daily_budget     NUMERIC(12, 2) NOT NULL CHECK (daily_budget > 0),
    duration_days    INT NOT NULL CHECK (duration_days BETWEEN 1 AND 30),
    goal             TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('pending', 'created', 'unknown', 'failed')),
    idempotency_key  TEXT NOT NULL,
    request_json     JSONB NOT NULL,
    response_json    JSONB NOT NULL DEFAULT '{}'::jsonb,
    error            TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, idempotency_key)
);

CREATE UNIQUE INDEX ad_boosts_one_active_idx
    ON ad_boosts (workspace_id, post_id, platform)
    WHERE post_id IS NOT NULL AND status IN ('pending', 'created', 'unknown');

CREATE INDEX ad_boosts_workspace_created_idx
    ON ad_boosts (workspace_id, created_at DESC);
