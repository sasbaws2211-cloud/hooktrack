-- Was a hook score made WITHOUT knowing how the post did?
--   TRUE  blind: judged on the wording alone (manual scores are blind by definition)
--   FALSE informed: the prompt included the post's own results, so the score partly reflects them
--   NULL  unknown: made before this was tracked; never used to fit thresholds
-- Fitting Scale/Tweak lines to informed scores is circular (a post scored 12 blind scored 26 once told it won).
ALTER TABLE score_snapshots ADD COLUMN IF NOT EXISTS blind BOOLEAN;
ALTER TABLE posts           ADD COLUMN IF NOT EXISTS score_blind BOOLEAN;

UPDATE score_snapshots SET blind = TRUE WHERE blind IS NULL AND score_by IN ('manual', 'imported');
CREATE INDEX IF NOT EXISTS score_snapshots_blind_idx ON score_snapshots (workspace_id, post_id, created_at DESC) WHERE blind IS TRUE;
