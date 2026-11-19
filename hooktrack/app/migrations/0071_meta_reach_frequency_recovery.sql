CREATE TABLE meta_reach_frequency_workflows (
    workflow_id TEXT PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    ad_connection_id TEXT NOT NULL,
    ad_account_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('quote_ready', 'reserve_pending', 'reserved', 'release_pending', 'released',
         'buy_pending', 'bought', 'unknown')),
    quote_id TEXT NOT NULL,
    reserved_id TEXT NOT NULL DEFAULT '',
    config_json JSONB NOT NULL,
    quote_json JSONB NOT NULL,
    reservation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    buy_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX meta_reach_frequency_workflows_workspace_account_updated_idx
    ON meta_reach_frequency_workflows (workspace_id, ad_connection_id, ad_account_id, updated_at DESC);
