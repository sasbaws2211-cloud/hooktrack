-- Client approval: an agency can require a client to sign off on a post before it goes out to that client's
-- own connected account. Off by default would defeat the point, so it is on by default per client and an
-- agency can turn it off for a client they trust to skip the round-trip.
ALTER TABLE clients ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE clients ADD COLUMN IF NOT EXISTS contact_email TEXT NOT NULL DEFAULT '';

-- One row per approval request. The token is the client's whole credential for the public page, exactly like
-- report_links: the link itself is the password, and deleting/deciding the row ends its use. Only one request
-- can be open ("pending") per post at a time, so an agency can't lose track of which link is the live one.
CREATE TABLE IF NOT EXISTS post_approvals (
    id           SERIAL PRIMARY KEY,
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id      INT  NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    token        TEXT NOT NULL UNIQUE,
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | cancelled
    platforms    TEXT NOT NULL DEFAULT '',
    schedule_at  TIMESTAMPTZ NOT NULL,
    client_note  TEXT NOT NULL DEFAULT '',
    requested_by INT REFERENCES users(id) ON DELETE SET NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at   TIMESTAMPTZ,
    viewed_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS post_approvals_post_idx ON post_approvals (post_id);
CREATE INDEX IF NOT EXISTS post_approvals_ws_idx ON post_approvals (workspace_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS post_approvals_one_pending_idx ON post_approvals (post_id) WHERE status = 'pending';
