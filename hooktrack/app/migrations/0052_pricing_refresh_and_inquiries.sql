-- Pricing for the first customers: Free, Solo, Pro and the lifetime deal. Agency comes off sale (an inactive plan cannot be bought,
-- but anyone already on it keeps it). Pro and Agency can be re-priced once about 100 customers have shown what people really use.
--
-- Solo is the entry paid plan: 3 tracked accounts and 1 publishing account for $12 a month. Its cost to us is small (data credits and
-- one publishing account); the AI allowance is capped because heavy use is the one cost not yet measured.
UPDATE plans SET name = 'Solo', description = 'For a creator tracking up to 3 accounts, with 1 account to publish to.',
       price_minor = 1200, max_accounts = 3, max_members = 1, max_ai_calls = 300, max_connected = 1, max_credits = 100, max_reports = 0
 WHERE code = 'solo';
UPDATE plans SET name = 'Solo (yearly)', description = 'For a creator tracking up to 3 accounts, with 1 account to publish to.',
       price_minor = 12000, max_accounts = 3, max_members = 1, max_ai_calls = 300, max_connected = 1, max_credits = 100, max_reports = 0
 WHERE code = 'solo_year';

-- Pro is the plan for anyone with a portfolio or a team, priced so the lifetime deal does not make everything else look weak:
-- 15 accounts, 3 publishing accounts, 5 teammates, 800 data credits, client reports and unlimited AI. Nobody on it loses anything.
UPDATE plans SET name = 'Pro', description = 'For a portfolio of accounts or a small team: 15 accounts, 3 publishing accounts and client reports.',
       price_minor = 3900, max_accounts = 15, max_members = 5, max_ai_calls = -1, max_connected = 3, max_credits = 800, max_reports = 5
 WHERE code = 'pro';
UPDATE plans SET name = 'Pro (yearly)', description = 'For a portfolio of accounts or a small team: 15 accounts, 3 publishing accounts and client reports.',
       price_minor = 39000, max_accounts = 15, max_members = 5, max_ai_calls = -1, max_connected = 3, max_credits = 800, max_reports = 5
 WHERE code = 'pro_year';

UPDATE plans SET active = FALSE WHERE code IN ('agency', 'agency_year');

-- "Need more?": what visitors tell us they need beyond the plans. Also emailed to the admin address; this is the backup.
CREATE TABLE IF NOT EXISTS sales_inquiries (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    accounts    INTEGER,
    team        INTEGER,
    platforms   TEXT NOT NULL DEFAULT '',
    budget      TEXT NOT NULL DEFAULT '',
    message     TEXT NOT NULL DEFAULT '',
    emailed     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
