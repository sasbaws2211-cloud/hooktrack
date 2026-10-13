-- Late keeps a disconnected account listed for about an hour. Remember what a workspace disconnected, so it
-- disappears from the list (and frees its slot) straight away.
CREATE TABLE IF NOT EXISTS late_disconnected (
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id   TEXT NOT NULL,
    platform     TEXT NOT NULL DEFAULT '',
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, account_id)
);
