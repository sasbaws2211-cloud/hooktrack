-- No free tier: a workspace gets a time-limited trial, then must subscribe.
-- The 'free' plan row stays only as an internal marker (a subscription row with plan 'free' means "never paid");
-- it is never sold or listed.
ALTER TABLE workspace_subscriptions ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;
UPDATE plans SET active = FALSE WHERE code = 'free';
