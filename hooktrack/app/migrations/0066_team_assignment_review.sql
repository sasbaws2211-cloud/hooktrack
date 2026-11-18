ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS assigned_to INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS team_review_status TEXT NOT NULL DEFAULT 'not_requested',
    ADD COLUMN IF NOT EXISTS team_review_note TEXT NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS team_review_requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS team_review_requested_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS team_reviewed_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS team_reviewed_at TIMESTAMPTZ;

ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_team_review_status_check;
ALTER TABLE posts ADD CONSTRAINT posts_team_review_status_check
    CHECK (team_review_status IN ('not_requested', 'pending', 'changes_requested', 'approved'));

CREATE INDEX IF NOT EXISTS posts_team_assignment_review_idx
    ON posts (workspace_id, assigned_to, team_review_status);
