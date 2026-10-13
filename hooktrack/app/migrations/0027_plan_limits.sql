-- Two limits that follow what actually costs money: connected publishing accounts and monthly data credits.
-- -1 = unlimited. extra_* are per-workspace add-ons on top of the plan (set by the operator for now).
ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_connected INT NOT NULL DEFAULT -1;
ALTER TABLE plans ADD COLUMN IF NOT EXISTS max_credits   INT NOT NULL DEFAULT -1;
ALTER TABLE workspace_subscriptions ADD COLUMN IF NOT EXISTS extra_connected INT NOT NULL DEFAULT 0;
ALTER TABLE workspace_subscriptions ADD COLUMN IF NOT EXISTS extra_credits   INT NOT NULL DEFAULT 0;

UPDATE plans SET max_connected = 3,  max_credits = 600  WHERE code = 'pro'    AND max_connected = -1 AND max_credits = -1;
UPDATE plans SET max_connected = 10, max_credits = 4000 WHERE code = 'agency' AND max_connected = -1 AND max_credits = -1;

-- the entry plan: a creator with a few accounts
INSERT INTO plans (code, name, description, price_minor, currency, interval, max_accounts, max_members, max_ai_calls,
                   max_connected, max_credits, active, position)
SELECT 'solo', 'Solo', 'For a creator with a few accounts.', 1900, 'USD', 'month', 3, 1, 100, 1, 150, TRUE, 0
WHERE NOT EXISTS (SELECT 1 FROM plans WHERE code = 'solo');
