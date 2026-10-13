-- One free trial per person (normalised email), not per workspace.
-- A claim survives deleting the workspace, so "create, abandon, recreate" does not give a new trial.
CREATE TABLE IF NOT EXISTS trial_claims (
    email         TEXT PRIMARY KEY,                    -- normalised: lower-case, +tag stripped, gmail dots removed
    workspace_id  INTEGER REFERENCES workspaces(id) ON DELETE SET NULL,
    claimed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS trial_eligible BOOLEAN NOT NULL DEFAULT TRUE;

-- Existing workspaces are grandfathered: they all keep their trial. Each owner's earliest workspace is
-- recorded as that person's claim, so only workspaces created from now on can be refused.
INSERT INTO trial_claims (email, workspace_id, claimed_at)
SELECT DISTINCT ON (norm) norm, wid, created_at FROM (
    SELECT CASE
             WHEN split_part(lower(u.email), '@', 2) IN ('gmail.com', 'googlemail.com')
               THEN replace(split_part(split_part(lower(u.email), '@', 1), '+', 1), '.', '') || '@gmail.com'
             ELSE split_part(split_part(lower(u.email), '@', 1), '+', 1) || '@' || split_part(lower(u.email), '@', 2)
           END AS norm,
           w.id AS wid, w.created_at
      FROM memberships m
      JOIN users u ON u.id = m.user_id
      JOIN workspaces w ON w.id = m.workspace_id
     WHERE m.role = 'owner') t
ORDER BY norm, created_at
ON CONFLICT (email) DO NOTHING;
