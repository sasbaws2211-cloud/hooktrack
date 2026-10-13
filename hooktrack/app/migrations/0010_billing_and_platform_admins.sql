-- Subscriptions + the standalone platform-admin console.

-- plans are defined by the operator in the admin console. A limit of -1 means unlimited.
CREATE TABLE IF NOT EXISTS plans (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    price_minor   INTEGER NOT NULL DEFAULT 0,          -- smallest currency unit (cents / pesewas / kobo)
    currency      TEXT NOT NULL DEFAULT 'USD',
    interval      TEXT NOT NULL DEFAULT 'month' CHECK (interval IN ('month', 'year')),
    max_accounts  INTEGER NOT NULL DEFAULT 3,
    max_members   INTEGER NOT NULL DEFAULT 2,
    max_ai_calls  INTEGER NOT NULL DEFAULT 50,         -- per calendar month
    active        BOOLEAN NOT NULL DEFAULT TRUE,       -- inactive plans cannot be bought; existing subscribers keep them
    position      INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls, position)
VALUES
    ('free',   'Free',   'Try it out on a couple of accounts.',          0,     'USD', 'month', 3,  2,  50,   0),
    ('pro',    'Pro',    'For a creator or small team running a portfolio.', 2900,  'USD', 'month', 15, 10, 1000, 1),
    ('agency', 'Agency', 'Unlimited accounts for agencies and networks.', 9900,  'USD', 'month', -1, -1, -1,   2)
ON CONFLICT (code) DO NOTHING;

-- one row per workspace; no row = the free plan
CREATE TABLE IF NOT EXISTS workspace_subscriptions (
    workspace_id        INTEGER PRIMARY KEY REFERENCES workspaces(id) ON DELETE CASCADE,
    plan_code           TEXT NOT NULL REFERENCES plans(code),
    status              TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'comp')),
    current_period_end  TIMESTAMPTZ,                    -- NULL on a comp plan = no end
    note                TEXT NOT NULL DEFAULT '',
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- every checkout attempt and its outcome; kept even if the workspace is deleted
CREATE TABLE IF NOT EXISTS payments (
    id              SERIAL PRIMARY KEY,
    workspace_id    INTEGER REFERENCES workspaces(id) ON DELETE SET NULL,
    workspace_name  TEXT NOT NULL DEFAULT '',
    plan_code       TEXT NOT NULL,
    amount_minor    INTEGER NOT NULL,
    currency        TEXT NOT NULL,
    provider        TEXT NOT NULL DEFAULT 'paystack',
    reference       TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'success', 'failed')),
    payer_email     TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    paid_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS payments_ws_idx ON payments (workspace_id, created_at DESC);

-- AI calls metered per workspace per month
CREATE TABLE IF NOT EXISTS ai_usage (
    workspace_id  INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    month         DATE NOT NULL,
    calls         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (workspace_id, month)
);

-- operators of the platform: their own accounts, separate from workspace users
CREATE TABLE IF NOT EXISTS platform_admins (
    id             SERIAL PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE,
    name           TEXT NOT NULL DEFAULT '',
    password_hash  TEXT NOT NULL,
    disabled       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin_sessions (
    token_hash  TEXT PRIMARY KEY,
    admin_id    INTEGER NOT NULL REFERENCES platform_admins(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
