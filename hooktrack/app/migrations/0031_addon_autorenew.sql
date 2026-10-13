-- Add-ons that renew by themselves with the card saved for the plan.
-- starts_at: a renewal begins when the purchase it renews ends, so the two never count together.
ALTER TABLE addon_purchases ADD COLUMN IF NOT EXISTS starts_at    TIMESTAMPTZ;
UPDATE addon_purchases SET starts_at = created_at WHERE starts_at IS NULL;
ALTER TABLE addon_purchases ALTER COLUMN starts_at SET DEFAULT now();
ALTER TABLE addon_purchases ALTER COLUMN starts_at SET NOT NULL;
ALTER TABLE addon_purchases ADD COLUMN IF NOT EXISTS auto_renew   BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE addon_purchases ADD COLUMN IF NOT EXISTS renew_state  TEXT    NOT NULL DEFAULT '';   -- '' | renewed | cancelled | failed
ALTER TABLE addon_purchases ADD COLUMN IF NOT EXISTS failures     INT     NOT NULL DEFAULT 0;
ALTER TABLE addon_purchases ADD COLUMN IF NOT EXISTS last_attempt TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS addon_purchases_renew_idx ON addon_purchases (expires_at) WHERE auto_renew AND renew_state = '';
