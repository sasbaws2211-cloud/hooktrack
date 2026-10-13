-- Activation funnel: the first time each workspace reaches each step, plus a weekly email digest.
CREATE TABLE IF NOT EXISTS activation_events (
    workspace_id INT  NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    event        TEXT NOT NULL,        -- account_added | first_sync | hook_sent | first_publish | first_verdict
    at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (workspace_id, event)
);

-- Back-fill from what already happened. Sample-data accounts (@demo_*) do not count as "added an account".
INSERT INTO activation_events (workspace_id, event, at)
SELECT workspace_id, 'account_added', MIN(created_at) FROM accounts
WHERE workspace_id IS NOT NULL AND handle NOT LIKE '@demo\_%'
GROUP BY workspace_id
ON CONFLICT DO NOTHING;

INSERT INTO activation_events (workspace_id, event, at)
SELECT workspace_id, 'first_sync', MIN(last_synced_at) FROM accounts
WHERE workspace_id IS NOT NULL AND last_synced_at IS NOT NULL AND handle NOT LIKE '@demo\_%'
GROUP BY workspace_id
ON CONFLICT DO NOTHING;

INSERT INTO activation_events (workspace_id, event, at)
SELECT workspace_id, 'hook_sent', MIN(created_at) FROM posts
WHERE workspace_id IS NOT NULL AND followup_of IS NOT NULL
GROUP BY workspace_id
ON CONFLICT DO NOTHING;

INSERT INTO activation_events (workspace_id, event, at)
SELECT workspace_id, 'first_publish', MIN(published_at) FROM posts
WHERE workspace_id IS NOT NULL AND published_at IS NOT NULL
GROUP BY workspace_id
ON CONFLICT DO NOTHING;

INSERT INTO activation_events (workspace_id, event, at)
SELECT workspace_id, 'first_verdict', MIN(analysed_at) FROM posts
WHERE workspace_id IS NOT NULL AND verdict <> '' AND analysed_at IS NOT NULL
GROUP BY workspace_id
ON CONFLICT DO NOTHING;

-- Weekly digest: the Monday (workspace time) a workspace was last handled, and a per-person opt-out.
ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS digest_week DATE;
ALTER TABLE users ADD COLUMN IF NOT EXISTS digest_off BOOLEAN NOT NULL DEFAULT FALSE;
