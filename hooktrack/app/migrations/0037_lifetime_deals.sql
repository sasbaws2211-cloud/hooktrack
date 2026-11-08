-- Lifetime deals (AppSumo): three lifetime tiers, the licenses that unlock them, and an audit log of every webhook.

-- a plan can now be a one-time lifetime tier
ALTER TABLE plans DROP CONSTRAINT IF EXISTS plans_interval_check;
ALTER TABLE plans ADD CONSTRAINT plans_interval_check CHECK (interval IN ('month', 'year', 'lifetime'));

-- a workspace on a lifetime license never lapses
ALTER TABLE workspace_subscriptions DROP CONSTRAINT IF EXISTS workspace_subscriptions_status_check;
ALTER TABLE workspace_subscriptions ADD CONSTRAINT workspace_subscriptions_status_check
    CHECK (status IN ('active', 'comp', 'lifetime'));

-- The tiers. price_minor is the display price only (AppSumo sets the real one); active = FALSE keeps them off the
-- Paystack checkout. Every limit is a monthly cap, never unlimited: a lifetime customer pays once but costs every month.
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, max_reports, active, position)
SELECT 'ltd_1', 'Lifetime Tier 1', 'For a creator with a few accounts.', 4900, 'USD', 'lifetime', 3, 1, 100, 1, 150, 0, FALSE, 10
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code = 'ltd_1');
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, max_reports, active, position)
SELECT 'ltd_2', 'Lifetime Tier 2', 'For a creator or small team running a portfolio.', 9900, 'USD', 'lifetime', 15, 5, 400, 3, 600, 1, FALSE, 11
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code = 'ltd_2');
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, max_reports, active, position)
SELECT 'ltd_3', 'Lifetime Tier 3', 'For agencies running many accounts.', 19900, 'USD', 'lifetime', 40, 10, 1000, 5, 1500, 10, FALSE, 12
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code = 'ltd_3');

-- One row per license key AppSumo has told us about.
CREATE TABLE IF NOT EXISTS lifetime_licenses (
    license_key      TEXT PRIMARY KEY,                 -- AppSumo's UUID
    tier             INT  NOT NULL DEFAULT 1,
    plan_code        TEXT NOT NULL DEFAULT 'ltd_1',
    status           TEXT NOT NULL DEFAULT 'inactive' CHECK (status IN ('inactive', 'active', 'deactivated', 'superseded')),
    workspace_id     INT REFERENCES workspaces(id) ON DELETE SET NULL,
    prev_license_key TEXT,                              -- set on an upgrade or downgrade
    last_event       TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    activated_at     TIMESTAMPTZ,
    claimed_at       TIMESTAMPTZ,
    deactivated_at   TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS lifetime_licenses_ws_idx ON lifetime_licenses (workspace_id);

-- Every webhook, as received (never a manual change: AppSumo forbids those), so any dispute can be traced.
CREATE TABLE IF NOT EXISTS appsumo_events (
    id           BIGSERIAL PRIMARY KEY,
    license_key  TEXT NOT NULL DEFAULT '',
    event        TEXT NOT NULL DEFAULT '',
    payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome      TEXT NOT NULL DEFAULT '',
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS appsumo_events_key_idx ON appsumo_events (license_key, received_at DESC);
