-- ===== tenancy & auth =====
CREATE TABLE IF NOT EXISTS workspaces (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    email         TEXT NOT NULL UNIQUE,              -- stored lowercase
    name          TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memberships (
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'member',     -- owner | admin | member
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    user_id      INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS invites (
    token_hash   TEXT PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    email        TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'member',
    invited_by   INT REFERENCES users(id) ON DELETE SET NULL,
    expires_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- per-workspace AI configuration; the API key is stored encrypted
CREATE TABLE IF NOT EXISTS workspace_ai (
    workspace_id INT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    api_key_enc  TEXT NOT NULL DEFAULT '',
    key_hint     TEXT NOT NULL DEFAULT '',
    model        TEXT NOT NULL DEFAULT '',
    provider     TEXT NOT NULL DEFAULT 'anthropic',   -- anthropic | openai | compatible
    base_url     TEXT NOT NULL DEFAULT '',            -- only used by 'compatible'
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE workspace_ai ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT 'anthropic';
ALTER TABLE workspace_ai ADD COLUMN IF NOT EXISTS base_url TEXT NOT NULL DEFAULT '';

-- ===== tenant data (every row carries workspace_id) =====
CREATE TABLE IF NOT EXISTS phones (
    id           SERIAL PRIMARY KEY,
    workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    color        TEXT NOT NULL DEFAULT '#6366f1',
    position     INT  NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS accounts (
    id            SERIAL PRIMARY KEY,
    workspace_id  INT REFERENCES workspaces(id) ON DELETE CASCADE,
    phone_id      INT REFERENCES phones(id) ON DELETE SET NULL,
    handle        TEXT NOT NULL,
    platform      TEXT NOT NULL DEFAULT 'TikTok',
    niche         TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',   -- active | warming | paused | killed
    times_per_day INT  NOT NULL DEFAULT 1,
    content_hook  TEXT NOT NULL DEFAULT '',
    model         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS daily_views (
    account_id   INT  NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE,
    day          DATE NOT NULL,
    views        BIGINT NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT 'manual',    -- manual | ai
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, day)
);

CREATE TABLE IF NOT EXISTS posts (
    id           SERIAL PRIMARY KEY,
    workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id   INT REFERENCES accounts(id) ON DELETE CASCADE,
    post_date    DATE NOT NULL,
    status       TEXT NOT NULL DEFAULT 'planned',   -- idea | planned | posted
    ai_prompt    TEXT NOT NULL DEFAULT '',
    hook         TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',
    caption      TEXT NOT NULL DEFAULT '',
    views        BIGINT NOT NULL DEFAULT 0,
    comments     INT NOT NULL DEFAULT 0,
    intent       BOOLEAN NOT NULL DEFAULT FALSE,
    v            INT,                                -- VSC: each /10
    s            INT,
    c            INT,
    score_notes  TEXT NOT NULL DEFAULT '',
    score_by     TEXT NOT NULL DEFAULT '',          -- '' | manual | ai | imported
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- settings used to be global; make them per workspace (old rows were only defaults)
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'settings')
       AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'settings' AND column_name = 'workspace_id') THEN
        DROP TABLE settings;
    END IF;
END $$;
CREATE TABLE IF NOT EXISTS settings (
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    key          TEXT NOT NULL,
    value        JSONB NOT NULL,
    PRIMARY KEY (workspace_id, key)
);

-- ===== migrations for databases created before multi-tenancy =====
ALTER TABLE phones      ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE;
ALTER TABLE accounts    ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE;
ALTER TABLE daily_views ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE;
ALTER TABLE posts       ADD COLUMN IF NOT EXISTS workspace_id INT REFERENCES workspaces(id) ON DELETE CASCADE;
ALTER TABLE accounts    DROP CONSTRAINT IF EXISTS accounts_handle_key;

CREATE UNIQUE INDEX IF NOT EXISTS accounts_ws_handle_idx ON accounts (workspace_id, handle);
CREATE INDEX IF NOT EXISTS phones_ws_idx ON phones (workspace_id);
CREATE INDEX IF NOT EXISTS daily_views_ws_idx ON daily_views (workspace_id, day);
CREATE INDEX IF NOT EXISTS posts_ws_date_idx ON posts (workspace_id, post_date);
CREATE INDEX IF NOT EXISTS posts_account_idx ON posts (account_id, post_date);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);
