-- SociaVault credits spent per workspace (an estimate from what each call reports), for the dashboard meter.
CREATE TABLE IF NOT EXISTS credit_usage (
    id           BIGSERIAL PRIMARY KEY,
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    day          DATE NOT NULL DEFAULT CURRENT_DATE,
    kind         TEXT NOT NULL,                 -- sync | read | comments | lookup
    credits      INT  NOT NULL
);
CREATE INDEX IF NOT EXISTS credit_usage_ws_day_idx ON credit_usage (workspace_id, day);
