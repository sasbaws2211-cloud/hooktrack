-- Where a person came from when they signed up: the name of the free-tool or guide page, carried in the web address
-- (?ref=tools-tiktok-analytics). Empty means they arrived directly. Kept with the account and removed with it.
ALTER TABLE users ADD COLUMN IF NOT EXISTS signup_ref TEXT NOT NULL DEFAULT '';
