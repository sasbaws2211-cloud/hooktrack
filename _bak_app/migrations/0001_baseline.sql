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
    status       TEXT NOT NULL DEFAULT 'scripted',  -- idea | scripted | filmed | posted | analysed (planned=legacy scripted)
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

-- ===== SociaVault connector =====
CREATE TABLE IF NOT EXISTS workspace_connectors (
    workspace_id        INT PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    sociavault_key_enc  TEXT NOT NULL DEFAULT '',
    key_hint            TEXT NOT NULL DEFAULT '',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS external_id   TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_synced_at TIMESTAMPTZ;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sync_enabled  BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE posts ADD COLUMN IF NOT EXISTS external_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS posts_ws_external_idx
    ON posts (workspace_id, account_id, external_id)
    WHERE external_id IS NOT NULL;

-- ===== richer scoring & A/B experiments =====
ALTER TABLE posts ADD COLUMN IF NOT EXISTS score_confidence REAL;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS score_reasoning TEXT NOT NULL DEFAULT '';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS parent_id INT REFERENCES posts(id) ON DELETE SET NULL;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS variant_label TEXT NOT NULL DEFAULT '';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS experiment_id INT;

CREATE TABLE IF NOT EXISTS experiments (
    id           SERIAL PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    account_id   INT REFERENCES accounts(id) ON DELETE SET NULL,
    name         TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT '',          -- shared body / format across variants
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE posts DROP CONSTRAINT IF EXISTS posts_experiment_id_fkey;
ALTER TABLE posts ADD CONSTRAINT posts_experiment_id_fkey
    FOREIGN KEY (experiment_id) REFERENCES experiments(id) ON DELETE SET NULL;

CREATE TABLE IF NOT EXISTS score_snapshots (
    id            SERIAL PRIMARY KEY,
    workspace_id  INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id       INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    v             INT,
    s             INT,
    c             INT,
    total         INT,
    decision      TEXT NOT NULL DEFAULT '',          -- decision under thresholds at score time
    confidence    REAL,
    reasoning     TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    score_by      TEXT NOT NULL DEFAULT '',
    thresholds    JSONB NOT NULL DEFAULT '{}',       -- {scale_vsc, mutate_vsc, ...}
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS score_snapshots_post_idx ON score_snapshots (post_id, created_at DESC);
CREATE INDEX IF NOT EXISTS experiments_ws_idx ON experiments (workspace_id);
CREATE INDEX IF NOT EXISTS posts_experiment_idx ON posts (experiment_id) WHERE experiment_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS posts_parent_idx ON posts (parent_id) WHERE parent_id IS NOT NULL;

-- ===== competitor / inspiration library =====
CREATE TABLE IF NOT EXISTS inspiration (
    id           SERIAL PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title        TEXT NOT NULL DEFAULT '',
    source_url   TEXT NOT NULL DEFAULT '',
    platform     TEXT NOT NULL DEFAULT '',
    hook_text    TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    image_path   TEXT NOT NULL DEFAULT '',   -- relative under uploads/
    tags         TEXT NOT NULL DEFAULT '',
    use_in_ai    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS inspiration_ws_idx ON inspiration (workspace_id, created_at DESC);
CREATE INDEX IF NOT EXISTS inspiration_ai_idx ON inspiration (workspace_id) WHERE use_in_ai;

-- ===== video generator add-on =====
CREATE TABLE IF NOT EXISTS workspace_addons (
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    addon_id     TEXT NOT NULL,                       -- 'video' | 'publish' | …
    enabled      BOOLEAN NOT NULL DEFAULT FALSE,
    provider     TEXT NOT NULL DEFAULT '',
    config_json  JSONB NOT NULL DEFAULT '{}',
    secrets_enc  TEXT NOT NULL DEFAULT '',
    key_hint     TEXT NOT NULL DEFAULT '',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, addon_id)
);

CREATE TABLE IF NOT EXISTS video_jobs (
    id           SERIAL PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id      INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',    -- pending | running | done | failed
    external_id  TEXT NOT NULL DEFAULT '',
    poll_url     TEXT NOT NULL DEFAULT '',
    result_url   TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    request_json JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS video_jobs_ws_status_idx ON video_jobs (workspace_id, status, updated_at);
CREATE INDEX IF NOT EXISTS video_jobs_post_idx ON video_jobs (post_id, id DESC);

ALTER TABLE posts ADD COLUMN IF NOT EXISTS video_url TEXT NOT NULL DEFAULT '';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS video_job_id INT;

-- ===== social publish add-on =====
CREATE TABLE IF NOT EXISTS publish_jobs (
    id           SERIAL PRIMARY KEY,
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    post_id      INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    provider     TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'pending',  -- pending | done | scheduled | failed
    external_id  TEXT NOT NULL DEFAULT '',
    error        TEXT NOT NULL DEFAULT '',
    request_json JSONB NOT NULL DEFAULT '{}',
    result_json  JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS publish_jobs_post_idx ON publish_jobs (post_id, id DESC);
CREATE INDEX IF NOT EXISTS publish_jobs_ws_idx ON publish_jobs (workspace_id, status);

ALTER TABLE posts ADD COLUMN IF NOT EXISTS external_post_id TEXT NOT NULL DEFAULT '';
ALTER TABLE posts ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS publish_platforms TEXT NOT NULL DEFAULT '';

ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS publish_jobs_queued_idx ON publish_jobs (status, scheduled_for)
  WHERE status = 'queued';

-- platform-wide settings (super-admin console)
CREATE TABLE IF NOT EXISTS platform_settings (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL DEFAULT 'null',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ===== trustworthy view data =====
-- NULL = views were never recorded for this post (the 0 is just the column default).
ALTER TABLE posts ADD COLUMN IF NOT EXISTS views_updated_at TIMESTAMPTZ;
UPDATE posts SET views_updated_at = created_at WHERE views_updated_at IS NULL AND views > 0;

CREATE OR REPLACE FUNCTION posts_touch_views() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.views > 0 AND NEW.views_updated_at IS NULL THEN NEW.views_updated_at := now(); END IF;
    ELSIF NEW.views IS DISTINCT FROM OLD.views THEN
        NEW.views_updated_at := now();
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS posts_touch_views_trg ON posts;
CREATE TRIGGER posts_touch_views_trg BEFORE INSERT OR UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION posts_touch_views();

-- lifetime view count per post per day, so a sync can derive views GAINED per day
CREATE TABLE IF NOT EXISTS post_view_snapshots (
    post_id      INT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    workspace_id INT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    day          DATE NOT NULL,
    views        BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (post_id, day)
);
CREATE INDEX IF NOT EXISTS post_view_snapshots_ws_idx ON post_view_snapshots (workspace_id, day);

-- ===== publish crash recovery =====
-- set right before the request goes to the provider: NULL = nothing was sent yet
ALTER TABLE publish_jobs ADD COLUMN IF NOT EXISTS dispatched_at TIMESTAMPTZ;
-- status 'unconfirmed' = request was sent but the outcome was never recorded (needs a human)

-- ===== workspace time zone (IANA name); "today" and week boundaries follow it =====
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'UTC';
