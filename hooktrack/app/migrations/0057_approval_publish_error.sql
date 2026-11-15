-- A client approving a post doesn't guarantee it actually got scheduled (addon not enabled, account not
-- connected, no video, etc). publish_post may fail before it ever creates a publish_jobs row, which is the
-- only thing the existing "Didn't go out" list looks at — so without a place of its own, that failure would
-- vanish with nobody ever told. decide() now stores it here instead.
ALTER TABLE post_approvals ADD COLUMN IF NOT EXISTS publish_error TEXT NOT NULL DEFAULT '';
