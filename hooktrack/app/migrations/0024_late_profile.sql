-- HookTrack's own Late (Zernio) account publishes for customers: one Late profile per workspace holds that
-- workspace's connected social accounts, so a post can only ever go to the accounts of its own workspace.
CREATE TABLE IF NOT EXISTS workspace_late (
    workspace_id INT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    profile_id   TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
