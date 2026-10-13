-- What the engine said about a post, day by day, so its calls can be checked against what happened later
-- (does a winner at day 3 still beat its account's normal at day 30?).
CREATE TABLE IF NOT EXISTS decision_log (
    post_id      INT  NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    day          DATE NOT NULL,
    age_days     INT  NOT NULL,
    multiple     NUMERIC,
    label        TEXT NOT NULL,
    decision     TEXT NOT NULL DEFAULT '',
    n_baseline   INT,
    confidence   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (post_id, day)
);
CREATE INDEX IF NOT EXISTS decision_log_ws_idx ON decision_log (workspace_id, age_days);
