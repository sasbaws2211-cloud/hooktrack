-- Email verification (gates the first spend of provider credits) and per-account sync-failure tracking
-- (feeds the operator's "sync failing for N accounts" alert).
ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
-- everyone who signed up before verification existed is treated as verified: it must never lock out a paying customer
UPDATE users SET email_verified_at = created_at WHERE email_verified_at IS NULL;

CREATE TABLE IF NOT EXISTS email_verifications (
    token_hash TEXT PRIMARY KEY,
    user_id    INT  NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS email_verifications_user_idx ON email_verifications (user_id);

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sync_fail_count INT NOT NULL DEFAULT 0;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sync_error TEXT NOT NULL DEFAULT '';
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sync_error_at TIMESTAMPTZ;
