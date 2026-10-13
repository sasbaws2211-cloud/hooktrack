-- Record which version of the Terms a person accepted, and when (at sign-up / account creation).
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS terms_version     TEXT NOT NULL DEFAULT '';
