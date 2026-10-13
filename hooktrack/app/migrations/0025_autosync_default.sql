-- Handles are meant to pull themselves in: auto-sync is on by default, and on for existing accounts.
ALTER TABLE accounts ALTER COLUMN sync_enabled SET DEFAULT TRUE;
UPDATE accounts SET sync_enabled = TRUE WHERE NOT sync_enabled;
