-- "This one is breaking out": each post is announced once, and people can switch the email off.
ALTER TABLE posts ADD COLUMN IF NOT EXISTS hot_alerted_at TIMESTAMPTZ;
ALTER TABLE users ADD COLUMN IF NOT EXISTS hot_off BOOLEAN NOT NULL DEFAULT FALSE;
