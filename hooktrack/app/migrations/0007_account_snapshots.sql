-- Audience size over time. Views only mean something relative to who could have seen them.
CREATE TABLE IF NOT EXISTS account_snapshots (
    account_id   INT  NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    day          DATE NOT NULL,
    followers    BIGINT,
    following    BIGINT,
    total_likes  BIGINT,
    video_count  INT,
    taken_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, day)
);
CREATE INDEX IF NOT EXISTS account_snapshots_ws_idx ON account_snapshots (workspace_id, day);
