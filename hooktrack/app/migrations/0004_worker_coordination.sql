-- leases: "only one worker runs this job at a time" (see app/leases.py)
CREATE TABLE IF NOT EXISTS job_leases (
    name        TEXT PRIMARY KEY,
    holder      TEXT NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    acquired_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- per-account "a sync is in progress" marker, so manual, scheduled and duplicate syncs never overlap
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS sync_claimed_at TIMESTAMPTZ;
