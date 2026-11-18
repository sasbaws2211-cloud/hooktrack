CREATE TABLE ad_campaign_drafts (
    id                  SERIAL PRIMARY KEY,
    workspace_id        INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    campaign_name       TEXT NOT NULL,
    ad_account_id       TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN
                            ('draft', 'creating', 'unknown', 'paused', 'active', 'launch_unknown', 'failed')),
    draft_json          JSONB NOT NULL,
    idempotency_key     TEXT NOT NULL,
    provider_requested_at TIMESTAMPTZ,
    request_json        JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_json       JSONB NOT NULL DEFAULT '{}'::jsonb,
    platform_campaign_id TEXT NOT NULL DEFAULT '',
    platform_ad_set_id  TEXT NOT NULL DEFAULT '',
    zernio_ad_id        TEXT NOT NULL DEFAULT '',
    pending_status      TEXT NOT NULL DEFAULT '' CHECK (pending_status IN ('', 'active', 'paused')),
    error               TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, idempotency_key)
);

CREATE INDEX ad_campaign_drafts_workspace_created_idx
    ON ad_campaign_drafts (workspace_id, created_at DESC);

CREATE TABLE ad_campaign_assets (
    id              SERIAL PRIMARY KEY,
    workspace_id    INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    file_name       TEXT NOT NULL DEFAULT '',
    file_size       BIGINT NOT NULL DEFAULT 0,
    content_type    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, id)
);

CREATE INDEX ad_campaign_assets_workspace_created_idx
    ON ad_campaign_assets (workspace_id, created_at DESC);
