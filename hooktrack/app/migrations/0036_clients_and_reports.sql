-- Clients (a brand an agency manages; accounts are grouped under one) and shareable read-only client reports.
CREATE TABLE IF NOT EXISTS clients (
    id           SERIAL PRIMARY KEY,
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS clients_ws_name_idx ON clients (workspace_id, lower(name));

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS client_id INT REFERENCES clients(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS accounts_client_idx ON accounts (client_id) WHERE client_id IS NOT NULL;

-- A report link shows one client (all their accounts) or one account, read-only, to anyone who has the link.
-- The token is the whole credential: long, random, and deleting the row revokes it at once.
CREATE TABLE IF NOT EXISTS report_links (
    id             SERIAL PRIMARY KEY,
    workspace_id   INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    token          TEXT NOT NULL UNIQUE,
    title          TEXT NOT NULL DEFAULT '',
    client_id      INT REFERENCES clients(id)  ON DELETE CASCADE,
    account_id     INT REFERENCES accounts(id) ON DELETE CASCADE,
    days           INT  NOT NULL DEFAULT 30,
    show_hooks     BOOLEAN NOT NULL DEFAULT TRUE,
    created_by     INT REFERENCES users(id) ON DELETE SET NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_viewed_at TIMESTAMPTZ,
    view_count     INT NOT NULL DEFAULT 0,
    CHECK (client_id IS NOT NULL OR account_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS report_links_ws_idx ON report_links (workspace_id);

-- Sharing a client report is what the bigger plans are for: how many active links a plan allows (-1 = unlimited).
ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_reports INT NOT NULL DEFAULT -1;
UPDATE plans SET max_reports = 0 WHERE code IN ('solo', 'solo_year');
UPDATE plans SET max_reports = 1 WHERE code IN ('pro', 'pro_year');
