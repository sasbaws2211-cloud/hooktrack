-- Decisions turn into work: a SCALE hook gets a scripted post (to film a matching video), a TWEAK (MUTATE) hook an idea.
-- followup_decision remembers which decision a hook last produced follow-up work for, so it happens once per decision.
ALTER TABLE posts
    ADD COLUMN IF NOT EXISTS followup_decision TEXT    NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS followup_of       INTEGER REFERENCES posts(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS posts_followup_of_idx ON posts (followup_of) WHERE followup_of IS NOT NULL;

-- Workspaces that already have decisions are baselined on the first run (their current Scale/Tweak hooks are not
-- back-filled); a workspace created after this migration starts with the flag off and gets follow-ups from its first sync.
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS followups_baseline_pending BOOLEAN NOT NULL DEFAULT false;
UPDATE workspaces SET followups_baseline_pending = true;
